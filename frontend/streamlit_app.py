import requests
import streamlit as st
from PIL import Image
import os
import datetime
import time
import base64
from audio_recorder_streamlit import audio_recorder
import tempfile

from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST, REGISTRY
from flask import Flask, Response
import threading

# API Endpoints
STT_API_URL = "http://traefik/stt"
LLM_API_URL = "http://traefik/llm"
TTS_API_URL = "http://traefik/tts"

STATUS_STT_API_URL = "http://traefik/task_status_stt"
STATUS_LLM_API_URL = "http://traefik/task_status_llm"
STATUS_TTS_API_URL = "http://traefik/task_status_tts"

GET_AUDIO_API_URL = "http://traefik/get_audio"

# 🔹 Check if metric already exists before creating it
if "frontend_request_count" not in REGISTRY._names_to_collectors:
    REQUEST_COUNT = Counter("frontend_request_count", "Total requests received")

app = Flask(__name__)

@app.route("/metrics")
def metrics():
    REQUEST_COUNT.inc()
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


def save_audio_file(audio_bytes, file_extension):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    file_name = f"audio_{timestamp}.{file_extension}"

    with open(file_name, "wb") as f:
        f.write(audio_bytes)

    return file_name


def autoplay_audio(file_path: str):
    with open(file_path, "rb") as f:
        data = f.read()
        b64 = base64.b64encode(data).decode()
        # Without play box
        md = f"""
            <audio autoplay style="display:none;">
                <source src="data:audio/mp3;base64,{b64}" type="audio/mp3">
            </audio>
            <script>
                var audio = document.querySelector("audio");
                audio.volume = 1.0;  // Set to max volume
                audio.play();
            </script>
        """
        st.markdown(md, unsafe_allow_html=True)

# def autoplay_audio(file_path: str):
#     """Autoplay audio using base64 encoding in Streamlit."""
#     try:
#         with open(file_path, "rb") as f:
#             data = f.read()
#             b64 = base64.b64encode(data).decode()
        
#         # HTML for autoplay + fallback play button
#         md = f"""
#         <audio controls autoplay>
#             <source src="data:audio/mp3;base64,{b64}" type="audio/mp3">
#             Your browser does not support the audio element.
#         </audio>
#         """
#         st.markdown(md, unsafe_allow_html=True)
#     except Exception as e:
#         st.error(f"Error playing audio: {e}")

def poll_task_status(service_url, task_id):
    """Polls the task status endpoint with exponential backoff."""
    wait_time = 2  # Start at 2 seconds
    max_wait = 10  # Max 10 seconds between retries

    with st.spinner(f'{service_url[-3:].upper()} Task is still processing...'):
        while True:
            response = requests.get(f"{service_url}/{task_id}")
            if response.status_code == 200:
                result = response.json()
                if result['status'] == 'SUCCESS':
                    return result['result']
                elif result['status'] == 'FAILURE':
                    st.error(f"Task Failed: {result['result']}")
                    return None
            time.sleep(wait_time)
            wait_time = min(wait_time * 2, max_wait)  # Exponential backoff


def logo_image():
    _, _, col3 = st.columns([1, 6, 1])
    with col3:
        logo_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "/app/images/logo.jpg"))
        st.image(Image.open(logo_dir), width=50)

# Image
imag_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "/app/images/sound_logo.jpg"))
st.image(Image.open(imag_dir), width=700)

st.title("The Efficiency of Microservices Architecture for AI Applications")
st.markdown("Click on the icon below to ask your question.")

col1, col2, col3 = st.columns([25, 20, 10])
with col1:
    print("")
with col2:

    audio_bytes = audio_recorder(pause_threshold=2.0, 
                                    sample_rate=44_100,
                                    text=" ",
                                    recording_color="#871605",
                                    neutral_color="#0d3db9")


if audio_bytes:
    st.audio(audio_bytes, format="audio/wav")
    audio_filename = save_audio_file(audio_bytes, "wav")
    #st.write("Saved the audio")
    #st.write(f"{audio_filename}")

    with open(audio_filename, "rb") as audio_file:
        stt_response = requests.post(STT_API_URL, files={"audio": audio_file})
        
    if stt_response.status_code == 200:
        task_id = stt_response.json()['task_id']
        transcription = poll_task_status(STATUS_STT_API_URL, task_id)
        if transcription:
            st.write(f"**Text transcription:** {transcription}")

            llm_response = requests.post(LLM_API_URL, json={"text": transcription})
            if llm_response.status_code == 200:
                task_id = llm_response.json()['task_id']
                llm_result = poll_task_status(STATUS_LLM_API_URL, task_id)
                if llm_result:
                    st.write(f"**LLM response:** {llm_result}")

                    tts_response = requests.post(TTS_API_URL, json={"text": llm_result})
                    if tts_response.status_code == 200:
                        task_id = tts_response.json()['task_id']
                        tts_result = poll_task_status(STATUS_TTS_API_URL, task_id)
                        if tts_result:
                            audio_url = f"{GET_AUDIO_API_URL}/{tts_result}"
                            tts_audio_response = requests.get(audio_url)
                                    
                            if tts_audio_response.status_code == 200:
                                # # Step 4: Save the MP3 file locally
                                local_audio_path = os.path.join("temp_audio", tts_result)
                                os.makedirs("temp_audio", exist_ok=True)  # Create folder if not exists
                                with open(local_audio_path, "wb") as f:
                                    f.write(tts_audio_response.content)  # Save the audio file

                                st.write("**Playing response in audio: :loud_sound:**")
                                autoplay_audio(local_audio_path)  # Play the saved MP3 file


# Logo
#logo_image()

# Run Flask for metrics in a separate thread
def run_flask():
    app.run(host="0.0.0.0", port=8506)

threading.Thread(target=run_flask, daemon=True).start()