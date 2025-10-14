import os
import time
import logging
from locust import HttpUser, task, between
from requests.exceptions import RequestException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AUDIO_FILE_PATH = "stt_audio_01.wav"
STATUS_STT_API_URL = "http://traefik/task_status_stt"
STATUS_LLM_API_URL = "http://traefik/task_status_llm"
STATUS_TTS_API_URL = "http://traefik/task_status_tts"

class MicroserviceUser(HttpUser):
    wait_time = between(1, 3)  # Simulated wait time between requests

    @task
    def full_workflow(self):
        """ 
        Simulates the workflow: 
        1. Speech-to-Text (STT) -> Get task ID and poll for completion
        2. Send STT output to LLM
        3. Send LLM output to TTS
        """
        stt_response = self.speech_to_text()
        if stt_response:
            llm_response = self.send_to_llm(stt_response)
            if llm_response:
                self.text_to_speech(llm_response)

    def speech_to_text(self):
        """ Sends an audio file to the STT service and retrieves the transcribed text. """
        logger.info("Starting STT request...")
        try:
            with open(AUDIO_FILE_PATH, "rb") as audio_file:
                with self.client.post(
                    "/stt", 
                    files={"audio": audio_file}, 
                    timeout=10, 
                    catch_response=True
                ) as response:
                    
                    logger.info(f"STT Response Status: {response.status_code}")
                    logger.info(f"STT Response Content: {response.text}")

                    if response.status_code == 200:
                        task_id = response.json().get("task_id")
                        if not task_id:
                            response.failure("No task_id returned from STT")
                            return None
                        
                        transcript = self.poll_task_status(STATUS_STT_API_URL, task_id)
                        return transcript
                    else:
                        response.failure(f"STT request failed: {response.status_code}")
                        return None

        except RequestException as e:
            logger.error(f"RequestException during STT request: {e}")
            return None

    def poll_task_status(self, status_url, task_id, initial_wait=2, max_wait=30, max_retries=20):
        """ Polls the task status endpoint with exponential backoff until completion or failure. """
        wait_time = initial_wait
        retries = 0

        while True:
            time.sleep(wait_time)
            with self.client.get(f"{status_url}/{task_id}", catch_response=True) as response:
                logger.info(f"Polling {status_url}/{task_id} - Status: {response.status_code}")

                if response.status_code == 200:
                    result = response.json()
                    if result["status"] == "SUCCESS":
                        return result.get("result", "")
                    elif result["status"] == "FAILURE":
                        response.failure(f"Task {task_id} failed: {result.get('result', 'No details')}")
                        return None
                else:
                    response.failure(f"Polling failed: {response.status_code}")

            wait_time = min(wait_time * 2, max_wait)
            retries += 1

    def send_to_llm(self, text):
        """ Sends transcribed text to the LLM service and returns the generated response. """
        logger.info("Sending text to LLM...")
        try:
            with self.client.post("/llm", json={"text": text}, catch_response=True) as response:
                if response.status_code == 200:
                    task_id = response.json().get("task_id")
                    if not task_id:
                        response.failure("No task_id returned from LLM")
                        return None
                    return self.poll_task_status(STATUS_LLM_API_URL, task_id)

                else:
                    response.failure(f"LLM request failed: {response.status_code}")
                    return None

        except RequestException as e:
            logger.error(f"RequestException during LLM request: {e}")
            return None

    def text_to_speech(self, text):
        """ Sends generated text to the TTS service and receives an audio file. """
        logger.info("Sending text to TTS...")
        try:
            with self.client.post("/tts", json={"text": text}, catch_response=True) as response:
                if response.status_code == 200:
                    task_id = response.json().get("task_id")
                    if not task_id:
                        response.failure("No task_id returned from TTS")
                        return None
                    return self.poll_task_status(STATUS_TTS_API_URL, task_id)

                else:
                    response.failure(f"TTS request failed: {response.status_code}")
                    return None

        except RequestException as e:
            logger.error(f"RequestException during TTS request: {e}")
            return None
