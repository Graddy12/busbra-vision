"""Production entry point: healthcheck becomes reachable only after model loading."""
from app import app

app.extensions["combined_predictor"].load()
