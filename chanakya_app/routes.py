import asyncio
import base64
import datetime
import os
import tempfile
import time
from flask import jsonify, render_template, request, redirect, url_for
from .app_setup import app
from .client_activity import update_client_activity, remove_inactive_clients, SAVE_INTERVAL, COUNT_FILE, active_clients
from .memory_management import retrieve_relevant_memories, list_all_memories, add_memory, delete_memory
from .query_refinement import get_query_refinement_chain
from .react_agent import get_chanakya_react_agent_with_history
from . import tool_loader
from .utils import get_plain_text_content
from . import utils as utils_module # to modify last_ai_response
import scripts.config as config
import scripts.stt_local as stt_local
import scripts.tts_local as tts_local
from langchain_core.agents import AgentAction


@app.route('/')
def index():
    update_client_activity(request.remote_addr)
    return render_template("index_full_chat.html", timestamp=datetime.datetime.now().timestamp(), wake_word=config.WAKE_WORD)

def background_thread():
    last_save_time = time.time()
    while True:
        current_time = time.time()
        remove_inactive_clients()
        if current_time - last_save_time >= SAVE_INTERVAL:
            try:
                with open(COUNT_FILE, 'w') as f:
                    f.write(str(len(active_clients)))
            except IOError as e:
                app.logger.error(f"Error writing count file: {e}")
            last_save_time = current_time
        time.sleep(1)

@app.route("/chat", methods=["POST"])
async def chat():
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    app.logger.info(f"--- ASYNC /CHAT (Loop: {id(loop)}) User: '{request.form['message']}' ---")

    update_client_activity(request.remote_addr)
    user_message = request.form["message"]

    if not user_message or not user_message.strip():
        return jsonify({"response": "Please provide a message."})
    try:
        current_query_refinement_chain = get_query_refinement_chain()
        refined_keywords_output = await asyncio.wait_for(current_query_refinement_chain.ainvoke({
                "user_question": user_message, "ai_response": utils_module.last_ai_response
            }), timeout=15)
        refined_keywords = get_plain_text_content(refined_keywords_output)

        memory_search_query = user_message
        if refined_keywords and refined_keywords.lower() not in ["none", ""]:
            memory_search_query += " " + refined_keywords
        relevant_memories = retrieve_relevant_memories(memory_search_query)

        current_dt_str = datetime.datetime.now().strftime("%Y-%m-%d, %I:%M:%S %p")
        dynamic_intro = f"""You are a conversational AI voice assistant named {config.WAKE_WORD}. Created by Dr. Rishabh Bajpai.
Your primary mode of interaction with the user is through voice (a phone call). Craft responses to be clear, concise, and natural-sounding.
Use shorter sentences and standard punctuation for natural pauses. Avoid complex sentence structures, markdown, or emojis.
Keep responses brief and conversational.
Do not repeat responses or ask unnecessary follow-up questions.
When you are asked something, try to understand the full intent. If a complex task requires multiple steps or tools, break it down into a sequence of actions and use the tools as needed for each step to achieve the final goal. Avoid asking the user for small details if they can be inferred or handled by tools.
Current date and time: {current_dt_str}"""

        memories_str = ""
        if relevant_memories:
            memories_str = "\n\nRelevant Memories (use if applicable for context or to avoid repeating work):\n" + "\n".join([f"- Date: {dt}, Memory: {mem}" for dt, mem in relevant_memories])

        dynamic_intro_and_memories_content = dynamic_intro + memories_str

        current_react_agent_with_history = get_chanakya_react_agent_with_history()

        app.logger.info(f"CHAT - Invoking Chanakya ReAct agent (ASYNC) input: '{user_message}'")
        response_payload = await asyncio.wait_for(current_react_agent_with_history.ainvoke(
            {
                "input": user_message,
                "dynamic_intro_and_memories": dynamic_intro_and_memories_content,
                "tools": tool_loader.mcp_tool_descriptions_for_llm,
                "tool_names": tool_loader.mcp_tool_names_for_llm
            },
            config={"configurable": {"session_id": "global_shared_session"}}
        ), timeout=20)
        app.logger.info(f"CHAT - Raw Chanakya ReAct AgentExecutor async response: {response_payload}")

        used_tools_in_turn = set()
        if "intermediate_steps" in response_payload and response_payload["intermediate_steps"]:
            for step in response_payload["intermediate_steps"]:
                if isinstance(step, tuple) and len(step) > 0 and isinstance(step[0], AgentAction):
                    used_tools_in_turn.add(step[0].tool)
            if used_tools_in_turn:
                app.logger.info(f"CHAT - Tools used in this turn: {list(used_tools_in_turn)}")
            else:
                app.logger.info("CHAT - No tools were explicitly called in the intermediate steps for this turn.")
        else:
            app.logger.info("CHAT - No intermediate_steps found in agent response (no tools called or not a ReAct agent).")

        utils_module.last_ai_response = get_plain_text_content(response_payload)
        app.logger.info(f"CHAT - Final text response: {utils_module.last_ai_response}")

        return jsonify({"response": utils_module.last_ai_response, "used_tools": list(used_tools_in_turn)})
    except RuntimeError as e:
        if "Event loop is closed" in str(e):
            app.logger.error(f"EVENT LOOP CLOSED ERROR in /chat: {e}", exc_info=True)
            return jsonify({"response": "Internal server error: Event loop issue."}), 500
        else:
            app.logger.error(f"Runtime error in /chat: {e}", exc_info=True)
            return jsonify({"response": f"Sorry, a runtime error occurred: {e}"}), 500
    except Exception as e:
        app.logger.error(f"Error in /chat endpoint: {e}", exc_info=True)
        return jsonify({"response": "Sorry, I encountered an error processing your message."}), 500

@app.route("/record", methods=["POST"])
async def record():
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    app.logger.info(f"--- ASYNC /RECORD (Loop: {id(loop)}) ---")

    update_client_activity(request.remote_addr)
    if 'audio' not in request.files: return jsonify({"error": "No audio file part."}), 400
    audio_file_storage = request.files['audio']
    if audio_file_storage.filename == '': return jsonify({"error": "No selected file."}), 400

    if audio_file_storage:
        temp_audio_file_path_stt = None
        voice_file_path = None
        temp_voice_file_path = None
        try:
            # Handle voice cloning file
            if 'voice_file' in request.files:
                voice_file = request.files['voice_file']
                if voice_file.filename != '':
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_f:
                        voice_file.save(temp_f.name)
                        voice_file_path = temp_f.name
                        temp_voice_file_path = temp_f.name
            elif 'voice_preset' in request.form:
                voice_preset = request.form['voice_preset']
                if voice_preset:
                    voice_file_path = os.path.join("tts", voice_preset)

            audio_data = audio_file_storage.read()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_f:
                temp_f.write(audio_data)
                temp_audio_file_path_stt = temp_f.name

            transcription = stt_local.transcribe_audio(temp_audio_file_path_stt, config.STT_SERVER_URL)
            if transcription is None or not transcription.strip():
                return jsonify({"error": "Could not understand audio."}), 400
            app.logger.info(f"RECORD - Transcription: '{transcription}'")
            user_message = transcription

            current_query_refinement_chain = get_query_refinement_chain()
            refined_keywords_output = await asyncio.wait_for(current_query_refinement_chain.ainvoke({
                "user_question": user_message, "ai_response": utils_module.last_ai_response
            }), timeout=15)

            refined_keywords = get_plain_text_content(refined_keywords_output)
            memory_search_query = transcription
            if refined_keywords and refined_keywords.lower() not in ["none", ""]: memory_search_query += " " + refined_keywords
            relevant_memories = retrieve_relevant_memories(memory_search_query)

            current_dt_str = datetime.datetime.now().strftime("%Y-%m-%d, %I:%M:%S %p")
            dynamic_intro = f"""You are a conversational AI voice assistant named {config.WAKE_WORD}. Created by Dr. Rishabh Bajpai.
Your primary mode of interaction with the user is through voice (a phone call). Craft responses to be clear, concise, and natural-sounding.
Use shorter sentences and standard punctuation for natural pauses. Avoid complex sentence structures, markdown, or emojis.
Keep responses brief and conversational.
Do not repeat responses or ask unnecessary follow-up questions.
When you are asked something, try to understand the full intent. If a complex task requires multiple steps or tools, break it down into a sequence of actions and use the tools as needed for each step to achieve the final goal. Avoid asking the user for small details if they can be inferred or handled by tools.
Current date and time: {current_dt_str}"""
            memories_str = ""
            if relevant_memories:
                memories_str = "\n\nRelevant Memories (use if applicable for context or to avoid repeating work):\n" + "\n".join([f"- Date: {dt}, Memory: {mem}" for dt, mem in relevant_memories])
            dynamic_intro_and_memories_content = dynamic_intro + memories_str

            current_react_agent_with_history = get_chanakya_react_agent_with_history()

            app.logger.info(f"RECORD - Invoking Chanakya ReAct agent (ASYNC) input: '{transcription}'")
            response_payload = await asyncio.wait_for(current_react_agent_with_history.ainvoke(
                {
                    "input": user_message,
                    "dynamic_intro_and_memories": dynamic_intro_and_memories_content,
                    "tools": tool_loader.mcp_tool_descriptions_for_llm,
                    "tool_names": tool_loader.mcp_tool_names_for_llm
                },
                config={"configurable": {"session_id": "global_shared_session"}}
            ), timeout=20)
            app.logger.info(f"RECORD - Raw Chanakya ReAct AgentExecutor async response: {response_payload}")
            utils_module.last_ai_response = get_plain_text_content(response_payload)
            app.logger.info(f"RECORD - Final text response: {utils_module.last_ai_response}")

            used_tools_in_turn = set()
            if "intermediate_steps" in response_payload and response_payload["intermediate_steps"]:
                app.logger.info(f"RECORD - Intermediate steps: {response_payload['intermediate_steps']}")
                for step in response_payload["intermediate_steps"]:
                    if isinstance(step, tuple) and len(step) > 0 and isinstance(step[0], AgentAction):
                        used_tools_in_turn.add(step[0].tool)

            bot_speech_audio_data_url = None
            if utils_module.last_ai_response:
                tts_audio_file_path_for_bot_response = tts_local.text_to_speech(utils_module.last_ai_response, config.TTS_ENGINE, config.TTS_SERVER_URL, voice_file_path=voice_file_path)
                if tts_audio_file_path_for_bot_response and os.path.exists(tts_audio_file_path_for_bot_response):
                    with open(tts_audio_file_path_for_bot_response, "rb") as f_audio: audio_bytes = f_audio.read()
                    audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
                    bot_speech_audio_data_url = f"data:audio/wav;base64,{audio_base64}"
                else: app.logger.error(f"TTS failed in /record")
            return jsonify({"response": utils_module.last_ai_response, "transcription": transcription, "audio_data_url": bot_speech_audio_data_url, "used_tools": list(used_tools_in_turn)})
        except RuntimeError as e:
             if "Event loop is closed" in str(e):
                app.logger.error(f"EVENT LOOP CLOSED ERROR in /record: {e}", exc_info=True)
                return jsonify({"response": "Internal server error: Event loop issue."}), 500
             else:
                app.logger.error(f"Runtime error in /record: {e}", exc_info=True)
                return jsonify({"response": f"Sorry, a runtime error occurred: {e}"}), 500
        except Exception as e:
            app.logger.error(f"Error processing /record: {e}", exc_info=True)
            return jsonify({"error": f"Server error: {str(e)}"}), 500
        finally:
            if temp_audio_file_path_stt and os.path.exists(temp_audio_file_path_stt):
                try: os.remove(temp_audio_file_path_stt)
                except OSError: pass
            if temp_voice_file_path and os.path.exists(temp_voice_file_path):
                try: os.remove(temp_voice_file_path)
                except OSError: pass
    return jsonify({"error": "Audio file not processed correctly."}), 400

@app.route("/play_response", methods=["POST"])
def play_response():
    update_client_activity(request.remote_addr)
    if utils_module.last_ai_response:
        try:
            audio_file_path = tts_local.text_to_speech(utils_module.last_ai_response, config.TTS_ENGINE, config.TTS_SERVER_URL)
            if not audio_file_path or not os.path.exists(audio_file_path):
                return jsonify({"error": "TTS audio file not found or not created."}), 500
            with open(audio_file_path, "rb") as f_audio: audio_bytes = f_audio.read()
            audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
            return jsonify({"audio_data_url": f"data:audio/wav;base64,{audio_base64}"})
        except Exception as e:
            app.logger.error(f"Error in /play_response: {e}", exc_info=True)
            return jsonify({"error": f"Server error: {str(e)}"}), 500
    return jsonify({"error": "No response available to play."})

@app.route('/list_voices')
def list_voices():
    """Lists the available voices in the tts directory."""
    try:
        tts_dir = "tts"
        if not os.path.exists(tts_dir) or not os.path.isdir(tts_dir):
            return jsonify({"error": "TTS directory not found."}), 404

        supported_extensions = ['.wav', '.mp3', '.ogg']
        voices = [f for f in os.listdir(tts_dir) if os.path.isfile(os.path.join(tts_dir, f)) and any(f.endswith(ext) for ext in supported_extensions)]
        return jsonify(voices)
    except Exception as e:
        app.logger.error(f"Error listing voices: {e}", exc_info=True)
        return jsonify({"error": "Server error listing voices."}), 500

@app.route('/voice_clone', methods=['POST'])
def voice_clone():
    """Clones a voice and returns the audio."""
    update_client_activity(request.remote_addr)
    text = request.form.get('text')
    if not text:
        return jsonify({"error": "No text provided."}), 400

    voice_file_path = None
    temp_voice_file_path = None
    try:
        if 'voice_file' in request.files:
            voice_file = request.files['voice_file']
            if voice_file.filename != '':
                with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_f:
                    voice_file.save(temp_f.name)
                    voice_file_path = temp_f.name
                    temp_voice_file_path = temp_f.name
        elif 'voice_preset' in request.form:
            voice_preset = request.form['voice_preset']
            if voice_preset:
                voice_file_path = os.path.join("tts", voice_preset)

        if not voice_file_path:
            return jsonify({"error": "No voice file provided."}), 400

        tts_audio_file_path = tts_local.text_to_speech(text, config.TTS_ENGINE, config.TTS_SERVER_URL, voice_file_path=voice_file_path)
        if tts_audio_file_path and os.path.exists(tts_audio_file_path):
            with open(tts_audio_file_path, "rb") as f_audio:
                audio_bytes = f_audio.read()
            audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
            return jsonify({"audio_data_url": f"data:audio/wav;base64,{audio_base64}"})
        else:
            return jsonify({"error": "TTS failed."}), 500
    except Exception as e:
        app.logger.error(f"Error in /voice_clone: {e}", exc_info=True)
        return jsonify({"error": f"Server error: {str(e)}"}), 500
    finally:
        if temp_voice_file_path and os.path.exists(temp_voice_file_path):
            try:
                os.remove(temp_voice_file_path)
            except OSError:
                pass

@app.route('/memory')
def memory_page():
    """Renders the memory management page."""
    memories = list_all_memories()
    return render_template('manage_memory.html', memories=memories)

@app.route('/add-memory', methods=['POST'])
def add_memory_route():
    """Handles adding a new memory."""
    memory_text = request.form.get('memory_text')
    if memory_text:
        add_memory(memory_text)
    return redirect(url_for('memory_page'))

@app.route('/delete-memory', methods=['POST'])
def delete_memory_route():
    """Handles deleting a memory."""
    memory_id = request.form.get('memory_id')
    if memory_id:
        delete_memory(memory_id)
    return redirect(url_for('memory_page'))
