from flask import Flask, request, jsonify, Response
from pymongo import MongoClient
import datetime
from celery import Celery
from celery.result import AsyncResult
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from shared.celery_config import app  # Import the Celery app
from kombu import Queue
#from gradio_client import Client
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import time

import os
from huggingface_hub import InferenceClient
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv(dotenv_path=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env")))

# Flask app initialization
app = Flask(__name__)

# Metrics definition
LLM_REQUEST_COUNT = Counter("llm_request_count", "Total de requisições ao LLM")
LLM_LATENCY = Histogram("llm_latency_seconds", "Tempo de inferência do LLM")
LLM_TEXT_SIZE = Histogram("llm_text_size_bytes", "Tamanho do texto recebido")

# MongoDB Configuration
mongo_config = {
    "host": "mongo",  # MongoDB host
    "port": 27017,
    "db_name": "llm_service"
}

# # Configure Celery
app.config['CELERY_BROKER_URL'] = 'redis://redis:6379/0'  # Redis URL for Celery broker
app.config['CELERY_RESULT_BACKEND'] = 'redis://redis:6379/0'  # Redis for result backend


# Define the default queue and custom queues
task_queues = (
    Queue('stt', routing_key='stt'),  # All tasks with stt.# go to stt
    Queue('llm', routing_key='llm'),  # All tasks with llm.# go to llm
    Queue('tts', routing_key='tts'),  # All tasks with llm.# go to llm
)

# Define task routes
task_routes = {
    'transcribe_audio': {'queue': 'stt', 'routing_key': 'stt'},
    'process_llm_query': {'queue': 'llm', 'routing_key': 'llm'},
    'generate_tts_audio': {'queue': 'tts', 'routing_key': 'tts'},
}

celery = Celery(
    "services.llm_api",
    broker=app.config['CELERY_BROKER_URL'],
    backend=app.config['CELERY_RESULT_BACKEND']  
)
celery.conf.update(
    task_queues=task_queues,
    task_routes=task_routes,
    broker_connection_retry_on_startup=True,  # Ensures retries on broker connection
)


# Celery Task to process the LLM query
@celery.task(bind=True, name="process_llm_query")
def process_llm_query(self, question):
    try:
        # Request metrics
        LLM_REQUEST_COUNT.inc()
        start_time = time.time()

        # Size metrics
        LLM_TEXT_SIZE.observe(len(question))


        client = InferenceClient("Qwen/Qwen2.5-72B-Instruct", token=os.getenv("HF_TOKEN"))
        resp = client.chat_completion(
            messages=[
                {"role": "system", "content": """You are a helpful and knowledgeable assistant. 
                Give clear, accurate, and concise answers. 
                When explaining something, keep it short and easy to understand. 
                Use examples only when they make the answer clearer. 
                Avoid unnecessary details or repetition."""},
                {"role": "user", "content": question},
            ],
            max_tokens=256,
            temperature=0,
        )
        answer = resp.choices[0].message.content


        # Latency metrics
        duration = time.time() - start_time
        LLM_LATENCY.observe(duration)

        # Save to MongoDB
        mongo_client = MongoClient(mongo_config["host"], mongo_config["port"])
        db = mongo_client[mongo_config["db_name"]]
        collection = db.llm_queries
        collection.insert_one({
            "question": question,
            "answer": answer,
            "timestamp": datetime.datetime.now()
        })

        print(f"LLM query saved to MongoDB: {answer}")

        return answer

    except Exception as e:
        raise self.retry(exc=e)
    
@app.route("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}

@app.route('/llm', methods=['POST'])
def llm_api():
    try:
        print("==============LLM==============")
        # Get question from request
        data = request.json
        question = data.get("text")

        # Call the Celery task to process the LLM query asynchronously
        task = process_llm_query.apply_async(args=[question], routing_key='llm')

        # Return the task ID for the client to check the status
        return jsonify({"message": "Query processing started", "task_id": task.id})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/task_status_llm/<task_id>', methods=['GET'])
def get_task_status(task_id):
    try:
        task = celery.AsyncResult(task_id)

        if task.state == 'PENDING':
            response = {"status": "PENDING", "result": None}
        elif task.state == 'SUCCESS':
            response = {"status": "SUCCESS", "result": task.result}
        elif task.state == 'FAILURE':
            response = {"status": "FAILURE", "result": str(task.result)}
        else:
            response = {"status": task.state, "result": None}

        return jsonify(response)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
#    app.run(host="localhost", port=8502, debug=True)
    app.run(host="0.0.0.0", port=8503, debug=True)
