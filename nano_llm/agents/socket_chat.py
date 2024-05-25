#!/usr/bin/env python3
import logging
import os
import re
import threading

from nano_llm import StopTokens
from nano_llm.web.socket_server import WebSocketServer
from nano_llm.utils import ArgParser, KeyboardInterrupt
from nano_llm.plugins import AutoASR
from nano_llm.agents.voice_chat import VoiceChat

class SocketChat(VoiceChat):
    """
    Agent for ASR → LLM → TTS pipeline using WebSockets.
    """
    def __init__(self, **kwargs):
        """
        Args:
            asr (NanoLLM.plugins.AutoASR|str): the ASR plugin instance or model name to connect with the LLM.
            llm (NanoLLM.Plugin|str): The LLM model plugin instance (like ChatQuery) or model name.
            tts (NanoLLM.plugins.AutoTTS|str): the TTS plugin instance (or model name)- if None, will be loaded from kwargs.
        """
        super().__init__(**kwargs)

        # Temp singleton instance until bot_function closures are fixed
        SocketChat.Instance = self

        # Add additional hooks to the voice components
        if self.asr:
            self.asr.add(self.on_asr_partial, AutoASR.OutputPartial, threaded=True)

        # Add the LLM model to the pipeline
        self.llm.add(self.on_llm_reply, threaded=True)

        # Add the TTS model to the pipeline
        if self.tts:
            self.tts_output.add(self.on_tts_samples, threaded=True)

        # Filters for sanitizing chat HTML
        self.web_regex = [
            (re.compile(r'`(.*?)`'), r'<code>\1</code>'),  # code blocks
            (re.compile(r'\*(.*?)\*'), r'*<i>\1</i>*'),    # emotives inside asterisks
        ]

        # Create the WebSocket server
        self.server = WebSocketServer(
            host=kwargs.get('host', 'localhost'),
            port=kwargs.get('port', 49000),
            msg_callback=self.on_message,
            **kwargs   # Why the fuck does this prevent the server from responding to WS client requests???
        )

    def send_chat_history(self):
        """
        Send chat history back to client.
        """
        
        # Get the latest LLM and ASR history
        history, num_tokens, max_context_len = self.llm.chat_state
        if self.asr and self.asr_history:
            history.append({'role': 'user', 'text': self.asr_history})
            
        def web_text(text):
            text = text.strip()
            text = text.strip('\n')
            text = text.replace('\n', '<br/>')
            text = text.replace('<s>', '')
            
            for stop_token in StopTokens:
                text = text.replace(stop_token, '')
                
            for regex, replace in self.web_regex:
                text = regex.sub(replace, text)
                
            return text
          
        def web_image(image):
            if not isinstance(image, str):
                if not hasattr(image, 'filename'):
                    return None
                image = image.filename
            return os.path.join(self.server.mounts.get(os.path.dirname(image), ''), os.path.basename(image))
            
        for entry in history:
            if 'text' in entry:
                entry['text'] = web_text(entry['text'])
            if 'image' in entry:
                entry['image'] = web_image(entry['image'])
                
        self.server.send_message({
            'chat_history': history,
            'chat_stats': {
                'num_tokens': num_tokens,
                'max_context_len': max_context_len,
            }
        })

    def on_message(self, msg, msg_type=0, metadata='', **kwargs):
        """
        WebSocket message handler from the client.
        """
        if msg_type == WebSocketServer.MESSAGE_JSON:
            logging.info(f"JSON received: {msg}")

            # # Handle client state message
            # if 'client_state' in msg:
            #     if msg['client_state'] == 'connected':
            #         client_init_msg = {
            #             'system_prompt': self.llm.history.system_prompt,
            #             'bot_functions': BotFunctions.generate_docs(prologue=False),
            #             'user_profile': '\n'.join(self.user_profile),
            #         }

            #         # Add TTS settings if available
            #         if self.tts:
            #             voices = self.tts.voices
                        
            #             if len(voices) > 20:
            #                 voices = natsort.natsorted(voices)
                  
            #             speakers = self.tts.speakers
                        
            #             if len(speakers) > 20:
            #                 speakers = natsort.natsorted(speakers)
                            
            #             client_init_msg.update({
            #                 'tts_voice': self.tts.voice, 
            #                 'tts_voices': voices, 
            #                 'tts_speaker': self.tts.speaker, 
            #                 'tts_speakers': speakers, 
            #                 'tts_rate': self.tts.rate
            #             })

            #         # Send the client initialization message
            #         self.server.send_message(client_init_msg)
                    
            # TODO: figure these out
            # if 'system_prompt' in msg:
            #     self.generate_system_prompt(msg['system_prompt'])
            # if 'function_calling' in msg:
            #     self.llm.history.functions = BotFunctions() if msg['function_calling'] else None
            #     self.generate_system_prompt()
            # if 'function_autodoc' in msg:
            #     self.enable_autodoc = msg['function_autodoc']
            #     self.generate_system_prompt()
            # if 'user_profile' in msg:
            #     self.user_profile = [x.strip() for x in msg['user_profile'].split('\n')]
            #     self.user_profile = [x for x in self.user_profile if x]
            #     self.generate_system_prompt()
            # if 'enable_profile' in msg:
            #     self.enable_profile = msg['enable_profile']
            #     self.generate_system_prompt()
            # if 'tts_voice' in msg and self.tts:
            #     self.tts.voice = msg['tts_voice']
            #     self.server.send_message({'tts_speaker': self.tts.speaker, 'tts_speakers': self.tts.speakers})
            # if 'tts_speaker' in msg and self.tts:
            #     self.tts.speaker = msg['tts_speaker']
            # if 'tts_rate' in msg and self.tts:
            #     self.tts.rate = float(msg['tts_rate'])

        # Handle text
        elif msg_type == WebSocketServer.MESSAGE_TEXT:
            self.on_interrupt()
            self.prompt(msg.strip('"'))

        # Handle audio
        elif msg_type == WebSocketServer.MESSAGE_AUDIO:
            if self.asr:
                self.asr(msg)

        # Handle image
        elif msg_type == WebSocketServer.MESSAGE_IMAGE:
            logging.info(f"recieved {metadata} image message {msg.size} -> {msg.filename}")
            self.llm(['/reset', msg.filename])
            
        # Handle unknown message type
        else:
            logging.warning(f"WebChat agent ignoring websocket message with unknown type={msg_type}")

    def on_asr_partial(self, text):
        """
        Update the chat history when a partial ASR transcript arrives.
        """
        self.send_chat_history()
        threading.Timer(1.5, self.on_asr_waiting, args=[text]).start()

    def on_asr_waiting(self, transcript):
        """
        If the ASR partial transcript hasn't changed, probably a misrecognized sound or echo (cancel it)
        """
        if self.asr_history == transcript:
            logging.warning(f"ASR partial transcript has stagnated, dropping from chat ({self.asr_history})")
            self.asr_history = None
            self.send_chat_history()
    
    def on_llm_reply(self, text):
        """
        Update the web chat history when the latest LLM response arrives.
        """
        self.send_chat_history()

    def on_tts_samples(self, audio):
        """
        Send audio samples to the client when they arrive.
        """
        self.server.send_message(audio, type=WebSocketServer.MESSAGE_AUDIO)

    def start(self):
        """
        Start the webserver & websocket listening in other threads.
        """
        print("STARTING WEBSOCKET SERVER")
        super().start()
        self.server.start()
        return self

    # Instance = None  # singleton instance

# Main
if __name__ == "__main__":

    # Parse args
    parser = ArgParser(extras=ArgParser.Defaults+['asr', 'tts', 'audio_output', 'web'])
    args = parser.parse_args()
    
    # Start the agent
    agent = SocketChat(**vars(args))
    interrupt = KeyboardInterrupt()
    agent.run() 
