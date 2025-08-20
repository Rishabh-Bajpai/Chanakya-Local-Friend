# tts_local supports coqui, chatterbox and piper

import requests
import urllib.parse
import os 

# chatterbox
import torchaudio as ta
from chatterbox.tts import ChatterboxTTS

def text_to_speech(text, tts_engine, server_url, output_filename="output.wav", voice_file_path=None):
    """
    Sends a text-to-speech request to the specified server and saves the audio to a file.

    Args:
        text (str): The text to convert to speech.
        tts_engine (str): The name of tts engine options chatterbox or coqui or piper
        server_url (str): The URL of the text-to-speech server.
        output_filename (str): The name of the file to save the audio to.
        voice_file_path (str, optional): The path to the voice file for cloning. Defaults to None.


    Returns:
        str: The path to the saved audio file if successful, None otherwise.
    """
    if not text or not text.strip():
        print("TTS Error: Input text is empty.")
        return None

    try:
        if (tts_engine=="chatterbox"):
            model = ChatterboxTTS.from_pretrained(device="cuda")
            if voice_file_path and os.path.exists(voice_file_path):
                AUDIO_PROMPT_PATH = voice_file_path
            else:
                AUDIO_PROMPT_PATH="tts/Michael_Scott_voice.mp3" # Voice for cloning
            wav = model.generate(text, audio_prompt_path=AUDIO_PROMPT_PATH)
            # Ensure the directory for output_filename exists if it's not just a filename
            output_dir = os.path.dirname(output_filename)
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir) # Create directory if it doesn't exist
            ta.save(output_filename, wav, model.sr)
        elif(tts_engine=="coqui"):
            if voice_file_path and os.path.exists(voice_file_path):
                # Use POST for voice cloning
                url = f"{server_url}"
                files = {'speaker_wav': (os.path.basename(voice_file_path), open(voice_file_path, 'rb'), 'audio/wav')}
                data = {'text': text}
                print(f"TTS: Requesting audio from: {url} with voice file") # Optional: for debugging
                response = requests.post(url, files=files, data=data, stream=True, timeout=30)
            else:
                # Encode the text for URL parameters
                encoded_text = urllib.parse.quote(text)
                speaker_id="p278"
                # Construct the full URL
                url = f"{server_url}?text={encoded_text}&speaker_id={speaker_id}"

                # Send the GET request
                print(f"TTS: Requesting audio from: {url}") # Optional: for debugging
                response = requests.get(url, stream=True, timeout=30)  # Use stream=True and add a timeout

            response.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)

            # Save the audio to a file
            # Ensure the directory for output_filename exists if it's not just a filename
            output_dir = os.path.dirname(output_filename)
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir) # Create directory if it doesn't exist

            with open(output_filename, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192): # write in chunks
                    if chunk: # filter out keep-alive new chunks
                        f.write(chunk)
        elif(tts_engine=="piper"):
            params = {
                "text": text,
                "voice": "en_US-ryan-high"
            }
            headers = {
                "accept": "audio/wav"
            }

            response = requests.get(server_url, params=params, headers=headers, stream=True)
            with open(output_filename, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                            f.write(chunk)
        print(f"TTS: Audio saved to {output_filename}")
        if os.path.exists(output_filename) and os.path.getsize(output_filename) > 0: # Check if file exists and is not empty
            return output_filename
        else:
            print(f"TTS Error: File {output_filename} was not created properly or is empty.")
            return None

    except requests.exceptions.Timeout:
        print(f"TTS Error: Request timed out to {server_url}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"TTS Error: Error during request to {server_url}: {e}")
        return None
    except Exception as e:
        print(f"TTS Error: An unexpected error occurred: {e}")
        return None


if __name__ == '__main__':
    # tts configurations, Please selete one of the tts engines
    # tts_engine
    tts_engine="piper" # coqui (human like, fast) or chatterbox (great voice cloning, slow) or piper (fastest)
    
    # Set server_url
    # IMPORTANT: For local testing, replace "localhost" with the actual IP address of your TTS server.
    if (tts_engine=="chatterbox"):
        server_url = None
    elif(tts_engine=="coqui"):
        server_url = "http://localhost:5002/api/tts"
    elif(tts_engine=="piper"):
        server_url = "http://localhost:5000/api/text-to-speech"

    # Debugging defautls
    # Set the default text you want to convert to speech
    text_to_convert = "Hi, I'm Chanakya. I'm fat and funny" 
    # Set the default output filename
    output_file = "chanakya_robot.wav"


    # Call the text_to_speech function
    saved_file_path = text_to_speech(text_to_convert, tts_engine, server_url, output_filename=output_file)

    if saved_file_path:
        print(f"Test successful: Speech saved to {saved_file_path}")
    else:
        print("Test failed: Speech could not be generated.")