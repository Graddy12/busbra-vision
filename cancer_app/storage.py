from __future__ import annotations

import base64
import copy
import json
import os
import re
import secrets
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from werkzeug.security import generate_password_hash

from .segmentation_policy import enforce_segmentation_policy, present_analysis


# Permanently reserved: old credentials and old sessions must never work again.
RETIRED_RECOVERY_USERNAME = "secours"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JsonStore:
    """Local demo storage, atomic replacements and a cross-process lock."""

    def __init__(self, root, admin_password=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.assets = self.root / "images"
        self.assets.mkdir(exist_ok=True)
        self.thread_lock = threading.RLock()
        with self.lock():
            for name in ("users", "analyses", "audit"):
                if not (self.root / f"{name}.json").exists():
                    self.write(name, [])
            key_path = self.root / ".session-key"
            if not key_path.exists():
                key_path.write_text(secrets.token_hex(32), encoding="ascii")
            self.secret_key = key_path.read_text(encoding="ascii").strip()
            users = self.read("users")
            if not users:
                password = admin_password or f"Demo-{secrets.token_hex(6)}!"
                users.append({
                    "id": uuid.uuid4().hex, "username": "admin", "name": "Administrateur",
                    "specialty": "Administration", "role": "admin", "active": True,
                    "password_hash": generate_password_hash(password), "auth_version": 1,
                    "created_at": now(),
                })
                self.write("users", users)
                if admin_password:
                    print("Compte initial admin créé avec le mot de passe fourni par la configuration privée.", flush=True)
                else:
                    print(f"Compte initial : admin | Mot de passe : {password}", flush=True)
            analyses = self.read("analyses")
            analyses_changed = False
            for row in analyses:
                if "second_review" not in row:
                    row["second_review"] = None
                    analyses_changed = True
                # Upgrade feedback created before the independent-review workflow.
                if ((row.get("feedback") or {}).get("is_incorrect")
                        and not row.get("second_review") and row.get("status") == "validated"):
                    row["status"] = "discordance_review"
                    analyses_changed = True
            if analyses_changed:
                self.write("analyses", analyses)
            legacy = self.root / "recovery.json"
            if legacy.exists():
                if not any(u["active"] and u["role"] == "admin" and self._ordinary_account(u) for u in users):
                    raise RuntimeError("Rétablissez un administrateur ordinaire actif avant de retirer l'ancien accès de secours.")
                legacy.unlink()
                self._audit({"id": "system-migration", "name": "Migration du système"},
                            "Suppression de l'ancien compte de secours", RETIRED_RECOVERY_USERNAME)

    @contextmanager
    def lock(self):
        with self.thread_lock, (self.root / ".store.lock").open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + 15
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Le stockage est occupé. Réessayez dans un instant.")
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def read(self, name):
        return json.loads((self.root / f"{name}.json").read_text(encoding="utf-8"))

    def write(self, name, value):
        target = self.root / f"{name}.json"
        descriptor, temporary = tempfile.mkstemp(prefix=f".{name}-", dir=self.root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _audit(self, actor, action, subject):
        events = self.read("audit")
        events.append({
            "date": now(), "actor": actor["name"], "actor_id": actor.get("id"),
            "actor_username": actor.get("username"), "recovery": bool(actor.get("is_recovery")),
            "action": action, "subject": subject,
        })
        self.write("audit", events[-2000:])

    def audit(self, actor, action, subject):
        with self.lock():
            self._audit(actor, action, subject)

    def users(self):
        with self.lock():
            return self.read("users")

    def user(self, ident):
        return next((u for u in self.users() if u["id"] == ident), None)

    @staticmethod
    def _ordinary_account(user):
        return (user.get("username") != RETIRED_RECOVERY_USERNAME
                and not user.get("is_recovery")
                and not str(user.get("id", "")).startswith("recovery-"))

    def login_user(self, username):
        if username == RETIRED_RECOVERY_USERNAME:
            return None
        with self.lock():
            return next((u for u in self.read("users") if u["username"] == username
                         and self._ordinary_account(u)), None)

    def session_user(self, ident):
        if str(ident).startswith("recovery-"):
            return None
        with self.lock():
            return next((u for u in self.read("users") if u["id"] == ident
                         and self._ordinary_account(u)), None)

    def save_user(self, actor, values, user_id=None):
        username = values.get("username", "").strip().lower()
        name = values.get("name", "").strip()
        specialty = values.get("specialty", "").strip()
        role = values.get("role", "doctor")
        password = values.get("password", "")
        if not re.fullmatch(r"[a-z0-9_.@-]{3,80}", username):
            raise ValueError("Identifiant : 3 à 80 caractères, lettres sans accents, chiffres, . _ @ ou -.")
        if username == RETIRED_RECOVERY_USERNAME:
            raise ValueError("Cet ancien identifiant a été retiré et ne peut plus être utilisé.")
        if not 2 <= len(name) <= 120 or len(specialty) > 120:
            raise ValueError("Vérifiez le nom et la spécialité (120 caractères maximum).")
        if role not in {"admin", "doctor"}:
            raise ValueError("Rôle inconnu.")
        if (not user_id or password) and len(password) < 10:
            raise ValueError("Le mot de passe doit contenir au moins 10 caractères.")
        with self.lock():
            users = self.read("users")
            if any(u["username"] == username and u["id"] != user_id for u in users):
                raise ValueError("Cet identifiant est déjà utilisé.")
            user = next((u for u in users if u["id"] == user_id), None)
            if user_id and not user:
                raise ValueError("Utilisateur introuvable.")
            if user and user["role"] == "admin" and role != "admin":
                if user["id"] == actor["id"] or sum(u["active"] and u["role"] == "admin" for u in users) <= 1:
                    raise ValueError("Vous ne pouvez pas retirer votre rôle ni celui du dernier administrateur actif.")
            if not user:
                user = {"id": uuid.uuid4().hex, "active": True, "auth_version": 1, "created_at": now()}
                users.append(user)
            user.update(username=username, name=name, specialty=specialty, role=role)
            if password:
                user["password_hash"] = generate_password_hash(password)
                if user_id:
                    user["auth_version"] += 1
            self.write("users", users)
            self._audit(actor, "Modification utilisateur" if user_id else "Création utilisateur", username)
            return user

    def toggle_user(self, actor, user_id):
        with self.lock():
            users = self.read("users")
            user = next((u for u in users if u["id"] == user_id), None)
            if not user:
                raise ValueError("Utilisateur introuvable.")
            if user["id"] == actor["id"]:
                raise ValueError("Vous ne pouvez pas désactiver votre propre compte.")
            if user["active"] and user["role"] == "admin" and sum(u["active"] and u["role"] == "admin" for u in users) <= 1:
                raise ValueError("Le dernier administrateur actif ne peut pas être désactivé.")
            user["active"] = not user["active"]
            user["auth_version"] += 1
            self.write("users", users)
            self._audit(actor, "Activation compte" if user["active"] else "Désactivation compte", user["username"])

    def list_analyses(self, actor, deleted=False):
        with self.lock():
            rows = self.read("analyses")
        return sorted((present_analysis(r) for r in rows if (actor["role"] == "admin" or r["owner_id"] == actor["id"]) and bool(r.get("deleted_at")) == deleted), key=lambda r: r["created_at"], reverse=True)

    def analysis(self, actor, ident, include_deleted=False):
        with self.lock():
            row = next((r for r in self.read("analyses") if r["id"] == ident), None)
        if not row or (actor["role"] != "admin" and row["owner_id"] != actor["id"]):
            return None
        if row.get("deleted_at") and not include_deleted:
            return None
        return present_analysis(row)

    def save_analysis(self, actor, image, result, reference):
        result = enforce_segmentation_policy(copy.deepcopy(result), image)
        ident = uuid.uuid4().hex
        folder = self.assets / ident
        folder.mkdir()
        image.save(folder / "original.png", format="PNG")
        images = {"original": "original.png"}
        for group, keys in (("segmentation", ("overlay", "mask")), ("gradcam", ("overlay", "heatmap"))):
            for key in keys:
                data = result.get(group, {}).get(f"{key}_data_url")
                if data:
                    filename = f"{group}_{key}.png"
                    (folder / filename).write_bytes(base64.b64decode(data.split(",", 1)[1], validate=True))
                    images[f"{group}_{key}"] = filename
        clean_result = copy.deepcopy(result)
        for group in ("segmentation", "gradcam"):
            clean_result[group] = {k: v for k, v in clean_result.get(group, {}).items() if not k.endswith("_data_url")}
        row = {
            "id": ident, "reference": reference or f"EX-{datetime.now():%Y%m%d}-{ident[:6].upper()}",
            "owner_id": actor["id"], "owner_name": actor["name"], "owner_specialty": actor["specialty"],
            "created_at": now(), "deleted_at": None, "status": "pending", "note": "",
            "feedback": None, "second_review": None,
            "reviewer": "", "reviewed_at": None, "images": images, "result": clean_result,
        }
        with self.lock():
            rows = self.read("analyses")
            rows.append(row)
            self.write("analyses", rows)
            self._audit(actor, "Nouvelle analyse", row["reference"])
        return row

    def save_feedback(self, actor, ident, assessment, corrected_class="", note="", reason=""):
        if assessment not in {"correct", "incorrect"}:
            raise ValueError("Indiquez si la prédiction est correcte ou incorrecte.")
        if assessment == "incorrect" and corrected_class not in {"normal", "benign", "malignant"}:
            raise ValueError("Choisissez la classe corrigée.")
        reason_labels = {
            "image_quality": "Qualité de l’image insuffisante",
            "wrong_class": "Classe proposée inadaptée",
            "segmentation": "Segmentation incohérente",
            "missing_context": "Contexte clinique manquant",
            "other": "Autre motif",
        }
        if assessment == "incorrect" and reason not in reason_labels:
            raise ValueError("Choisissez le motif du désaccord.")
        if assessment == "correct":
            corrected_class = ""
            reason = ""
        note = note.strip()
        if len(note) > 5000:
            raise ValueError("Le commentaire doit contenir au maximum 5 000 caractères.")
        labels = {"normal": "Normale", "benign": "Bénigne", "malignant": "Maligne"}
        with self.lock():
            rows = self.read("analyses")
            row = next((item for item in rows if item["id"] == ident), None)
            if not row or row.get("deleted_at") or (actor["role"] != "admin" and row["owner_id"] != actor["id"]):
                raise ValueError("Analyse introuvable.")
            reviewed_at = now()
            feedback = {
                "assessment": assessment,
                "is_incorrect": assessment == "incorrect",
                "corrected_class": corrected_class,
                "corrected_label": labels.get(corrected_class, ""),
                "reason": reason,
                "reason_label": reason_labels.get(reason, ""),
                "note": note,
                "reviewer_id": actor["id"],
                "reviewer": actor["name"],
                "reviewed_at": reviewed_at,
            }
            row.update(feedback=feedback,
                       second_review=None,
                       status="discordance_review" if assessment == "incorrect" else "validated",
                       note=note,
                       reviewer=actor["name"], reviewed_at=reviewed_at)
            self.write("analyses", rows)
            self._audit(actor, "Feedback médical", row["reference"])
            return feedback

    def list_discordances(self, actor):
        """Return the shared, authenticated second-reading queue only."""
        del actor  # Authentication is enforced before this narrow team view is called.
        with self.lock():
            rows = self.read("analyses")
        visible = [present_analysis(row) for row in rows
                   if not row.get("deleted_at") and (row.get("feedback") or {}).get("is_incorrect")]
        return sorted(visible, key=lambda row: row["created_at"], reverse=True)

    def reviewable_analysis(self, actor, ident):
        """Allow team access only to a case explicitly placed in second reading."""
        del actor
        with self.lock():
            row = next((item for item in self.read("analyses") if item["id"] == ident), None)
        if not row or row.get("deleted_at") or not (row.get("feedback") or {}).get("is_incorrect"):
            return None
        return present_analysis(row)

    def save_second_review(self, actor, ident, outcome, note=""):
        outcomes = {
            "system_confirmed": "Prédiction du système confirmée",
            "medical_correction_confirmed": "Correction médicale confirmée",
            "additional_exam_required": "Examen complémentaire requis",
        }
        if actor.get("role") != "doctor":
            raise PermissionError("La seconde lecture doit être signée par un médecin.")
        if outcome not in outcomes:
            raise ValueError("Choisissez une conclusion de seconde lecture.")
        note = note.strip()
        if not 2 <= len(note) <= 5000:
            raise ValueError("Justifiez la seconde lecture (2 à 5 000 caractères).")
        labels = {"normal": "Normale", "benign": "Bénigne", "malignant": "Maligne"}
        with self.lock():
            rows = self.read("analyses")
            row = next((item for item in rows if item["id"] == ident), None)
            if not row or row.get("deleted_at") or row.get("status") != "discordance_review":
                raise ValueError("Cette discordance n’est plus disponible pour une seconde lecture.")
            feedback = row.get("feedback") or {}
            first_reviewer_id = feedback.get("reviewer_id")
            same_reviewer = first_reviewer_id == actor["id"] or (
                not first_reviewer_id and feedback.get("reviewer") == actor.get("name")
            )
            if same_reviewer:
                raise PermissionError("La seconde lecture doit être effectuée par un autre médecin.")
            if outcome == "system_confirmed":
                final_class = row["result"]["classification"]["code"]
            elif outcome == "medical_correction_confirmed":
                final_class = feedback.get("corrected_class", "")
            else:
                final_class = ""
            second_review = {
                "outcome": outcome,
                "outcome_label": outcomes[outcome],
                "final_class": final_class,
                "final_label": labels.get(final_class, ""),
                "note": note,
                "reviewer_id": actor["id"],
                "reviewer": actor["name"],
                "reviewed_at": now(),
            }
            row.update(second_review=second_review, status=outcome,
                       reviewer=actor["name"], reviewed_at=second_review["reviewed_at"])
            self.write("analyses", rows)
            self._audit(actor, "Conclusion de seconde lecture", row["reference"])
            return second_review

    def update_analysis(self, actor, ident, action, note="", status="pending"):
        with self.lock():
            rows = self.read("analyses")
            row = next((r for r in rows if r["id"] == ident), None)
            if not row or (actor["role"] != "admin" and row["owner_id"] != actor["id"]):
                raise ValueError("Analyse introuvable.")
            if action == "delete":
                row["deleted_at"] = now()
                label = "Mise en corbeille"
            elif action == "restore":
                row["deleted_at"] = None
                label = "Restauration analyse"
            elif action == "review" and not row["deleted_at"]:
                if (row.get("feedback") or {}).get("is_incorrect"):
                    raise ValueError("Cette discordance doit être conclue dans le circuit de seconde lecture.")
                if status not in {"pending", "validated"} or len(note) > 5000:
                    raise ValueError("Statut ou observation invalide (5 000 caractères maximum).")
                row.update(status=status, note=note.strip(), reviewer=actor["name"], reviewed_at=now())
                label = "Revue de l'analyse"
            else:
                raise ValueError("Action indisponible.")
            self.write("analyses", rows)
            self._audit(actor, label, row["reference"])

    def events(self):
        with self.lock():
            return list(reversed(self.read("audit")[-100:]))
