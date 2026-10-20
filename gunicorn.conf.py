import os

bind = f"0.0.0.0:{int(os.getenv('PORT', '8080'))}"
# One model copy and one rate-limiter state for this single-instance JSON demo.
workers = 1
worker_class = "gthread"
threads = 2
timeout = 300
graceful_timeout = 30
preload_app = True
accesslog = "-"
errorlog = "-"
capture_output = True
