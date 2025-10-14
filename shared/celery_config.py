from celery import Celery

# Celery app configuration
app = Celery(
    'tasks',  # Name for the Celery instance
    broker='redis://redis:6379/0',  # Redis broker (running in Docker Compose)
    backend='redis://redis:6379/0',  # Redis result backend
    broker_connection_retry_on_startup = True
)

# from celery import Celery

# # Celery app configuration
# def make_celery(app):
#     celery = Celery(
#         app.name, 
#         broker=app.config['CELERY_BROKER_URL'],
#         backend=app.config['CELERY_RESULT_BACKEND']
#     )
#     celery.conf.update(app.config)
#     return celery

