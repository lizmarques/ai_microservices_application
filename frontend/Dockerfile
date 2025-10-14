# Use the official Python image as the base image
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Install system dependencies for sounddevice, PortAudio, and Pulseaudio
RUN apt-get update && apt-get install -y \
    #libportaudio2 \
    #libsndfile1 \
    ffmpeg \
    && apt-get clean


# Copy the requirements file into the container
COPY frontend/requirements_streamlit.txt ./requirements_streamlit.txt

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements_streamlit.txt

# Copy the entire frontend directory contents to the container
COPY ./frontend . 

# Copy the images folder into the container
COPY images/ /app/images/

# Expose the default Streamlit port
EXPOSE 8505

# Run the Streamlit app
CMD ["streamlit", "run", "streamlit_app.py", "--server.port=8505", "--server.enableCORS=false", "--server.enableXsrfProtection=false"]
