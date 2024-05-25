import datetime
import json
import logging
import os
import PIL
import ssl
import struct
import threading
import time

from websockets.sync.server import serve as websocket_serve
from websockets.exceptions import ConnectionClosed

class WebSocketServer():
    """
    This class is a simple websocket server that listens for incoming websocket connections.
    It will call a user-defined callback function whenever a new message is received.
    
    Header format:
    - msg_id (8 bytes)
    - timestamp (8 bytes)
    - magic_number (2 bytes)
    - msg_type (2 bytes)
    - payload_size (4 bytes)
    - metadata (8 bytes)

    """

    # Constants
    MESSAGE_JSON = 0
    MESSAGE_TEXT = 1
    MESSAGE_BINARY = 2
    MESSAGE_FILE = 3
    MESSAGE_AUDIO = 4
    MESSAGE_IMAGE = 5
    HEADER_MAGIC_NUMBER = 42

    def __init__(
        self, 
        host='0.0.0.0',
        port=49000,
        ssl_cert=None, 
        ssl_key=None,
        upload_dir=None,
        msg_callback=None,
        **kwargs
    ):

        # Save parameters
        self.host = host
        self.port = port
        self.ssl_key = ssl_key
        self.ssl_cert = ssl_cert
        self.upload_dir = upload_dir
        self.msg_callback = msg_callback
        self.kwargs = kwargs

        # # Save port to kwargs
        self.kwargs['ws_port'] = port

        # Save callback in list
        if self.msg_callback and not isinstance(self.msg_callback, list):
            self.msg_callback = [self.msg_callback]

        # Initialize variables
        self.msg_count_rx = 0
        self.msg_count_tx = 0

        # Configure SSL
        self.ssl_context = None
        if self.ssl_cert and self.ssl_key:
            self.ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            self.ssl_context.load_cert_chain(certfile=self.ssl_cert, keyfile=self.ssl_key)

        # Set up websocket server
        self.websocket = None
        self.ws_server = websocket_serve(
            self.on_websocket, 
            host=self.host, 
            port=self.port, 
            ssl_context=self.ssl_context, 
            max_size=None
        )

        # Run websocket server thread
        self.ws_thread = threading.Thread(
            target=lambda: self.ws_server.serve_forever(),
        )

        # https://stackoverflow.com/a/52282788
        logging.getLogger('asyncio').setLevel(logging.INFO)
        logging.getLogger('asyncio.coroutines').setLevel(logging.INFO)
        logging.getLogger('websockets.server').setLevel(logging.INFO)
        logging.getLogger('websockets.protocol').setLevel(logging.INFO)

    def start(self):
        """
        Call this to start the webserver listening for new connections.
        It will start new worker threads and then return control to the user.
        """
        logging.info(f"starting websocket server @ ws://{self.host}:{self.port}")
        self.ws_thread.start()

    def on_websocket(self, websocket):  
        """
        This function is called whenever a new websocket connection is made.
        It will create a new thread to handle the connection.
        """    
        self.websocket = websocket  # TODO handle multiple clients
        remote_address = websocket.remote_address

        logging.info(f"new websocket connection from {remote_address}")

        '''
        # empty the queue from before the connection was made
        # (otherwise client will be flooded with old messages)
        # TODO implement self.connected so the ws_queue doesn't grow so large without webclient 
        # connected...
        while True:
            try:
                self.ws_queue.get(block=False)
            except queue.Empty:
                break
        '''

        try:
            self.websocket_listener(websocket)
        except ConnectionClosed as closed:
            logging.info(f"websocket connection with {remote_address} was closed")
            if self.websocket == websocket: # if the client refreshed, the new websocket may already be created
                self.websocket = None
        
        if self.msg_callback:
            for callback in self.msg_callback:
                callback({'client_state': 'connected'}, 0, int(time.time()*1000))

    def websocket_listener(self, websocket):
        logging.info(f"listening on websocket connection from {websocket.remote_address}")

        header_size = 32
            
        # Loop to listen for new messages
        while True:

            # Receive a new message
            msg = websocket.recv()

            # Print the whole message divided into hex bytes
            print("Message:", ' '.join(f"{byte:02x}" for byte in msg))
            
            # If the message is a string, log it and continue
            if isinstance(msg, str):
                logging.warning(f'dropping text-mode websocket message from' \
                    f'{websocket.remote_address} "{msg}"')
                continue
                
            # If the message is empty, log it and continue
            if len(msg) <= header_size:
                logging.warning(f"dropping invalid websocket message from " \
                    f"{websocket.remote_address} (size={len(msg)})")
                continue
                
            # Unpack the message header
            msg_id, timestamp, magic_number, msg_type, payload_size = \
                struct.unpack_from('!QQHHI', msg)

            # TEST: Print the header parts divided into hex bytes
            print("Header:", ' '.join(f"{byte:02x}" for byte in msg[:24]))

            # Extract and decode the metadata
            metadata = msg[24:32].split(b'\x00')[0].decode()
            
            # Print the metadata divided into hex bytes
            print("Metadata:", ' '.join(f"{byte:02x}" for byte in msg[24:32]))

            # Check if the header contains the magic number
            if magic_number != self.HEADER_MAGIC_NUMBER:
                logging.warning(f"dropping invalid websocket message from "\
                    f"{websocket.remote_address} (magic_number={magic_number} size={len(msg)})")
                continue

            # Check the message order
            if msg_id != self.msg_count_rx:
                logging.debug(f"recieved websocket message from {websocket.remote_address} " \
                    f"with out-of-order ID {msg_id}  (last={self.msg_count_rx})")
                self.msg_count_rx = msg_id
                
            # Increment the message counter
            self.msg_count_rx += 1

            # Check the payload size
            msgPayloadSize = len(msg) - header_size
            
            # Check the payload size
            if payload_size != msgPayloadSize:
                logging.warning(f"recieved invalid websocket message from " \
                    f"{websocket.remote_address} (payload_size={payload_size} " \
                    f"actual={msgPayloadSize}")
            
            payload = msg[header_size:]
            
            # Decode the payload based on the message type
            if msg_type == self.MESSAGE_JSON:
                payload = json.loads(payload)
            elif msg_type == self.MESSAGE_TEXT:
                payload = payload.decode('utf-8')
                
            # save uploaded files/images to the upload dir
            filename = None
                
            # Save file or image
            if self.upload_dir and \
                metadata and \
                (msg_type == WebSocketServer.MESSAGE_FILE or msg_type == WebSocketServer.MESSAGE_IMAGE):
                filename = f"{datetime.datetime.utcfromtimestamp(timestamp/1000).strftime('%Y%m%d_%H%M%S')}.{metadata}"
                filename = os.path.join(self.upload_dir, filename)
                threading.Thread(target=self.save_upload, args=[payload, filename]).start()
             
            # decode images in-memory
            if msg_type == self.MESSAGE_IMAGE:
                try:
                    payload = PIL.Image.open(io.BytesIO(payload))
                    if filename:
                        payload.filename = filename
                except Exception as err:
                    print(err)
                    logging.error(f"failed to load invalid/corrupted {metadata} image uploaded " \
                        "from client")
                    
            self.on_message(
                payload, 
                payload_size=payload_size, 
                msg_type=msg_type,
                msg_id=msg_id, 
                metadata=metadata, 
                timestamp=timestamp, 
                path=filename
            )

    def save_upload(self, payload, path):
        """
        Save a file or image upload to the server.
        """
        logging.debug(f"saving client upload to {path}")
        with open(path, 'wb') as file:
            file.write(payload)

    def on_message(
        self, 
        payload, 
        payload_size=None, 
        msg_type=MESSAGE_JSON, 
        msg_id=None, 
        metadata=None, 
        timestamp=None, 
        path=None, 
        **kwargs
    ):
        """
        Handler for recieved websocket messages. Implement this in a subclass to process messages,
        otherwise ``msg_callback`` needs to be provided during initialization.
        
        Args:
        
          payload (dict|str|bytes): If this is a JSON message, will be a dict.
                                    If this is a text message, will be a string.
                                    If this is a binary message, will be a bytes array.  
                                      
          payload_size (int): size of the payload (in bytes)              
          msg_type (int): MESSAGE_JSON (0), MESSAGE_TEXT (1), MESSAGE_BINARY (2)
          msg_id (int): the monotonically-increasing message ID number
          metadata (str): message-specific string or other data
          timestamp (int): time that the message was sent
          path (str): if this is a file or image upload, the file path on the server
        """
        if self.msg_callback:
            for callback in self.msg_callback:
                callback(
                    payload, 
                    payload_size=payload_size, 
                    msg_type=msg_type, 
                    msg_id=msg_id, 
                    metadata=metadata, 
                    timestamp=timestamp, 
                    path=path, 
                    **kwargs
                )
        else:
           raise NotImplementedError(f"{type(self)} did not implement on_message or have a msg_callback provided")

    def send_message(self, payload, type=None, timestamp=None):
        """
        Send a message to the connected client.
        """

        # Get the timestamp for the header
        if timestamp is None:
            timestamp = time.time() * 1000
         
        # Set the payload type
        encoding = None
        if type is None:
            if isinstance(payload, str):
                type = WebSocketServer.MESSAGE_TEXT
                encoding = 'utf-8'
            elif isinstance(payload, bytes):
                type = WebSocketServer.MESSAGE_BINARY
            else:
                type = WebSocketServer.MESSAGE_JSON
                encoding = 'ascii'

        # Make sure the websocket is connected
        if self.websocket is None:
            logging.debug(f"send_message() - no websocket clients connected, dropping " \
                "{self.msg_type_str(type)} message")
            return

        # Log message
        if logging.getLogger().isEnabledFor(logging.DEBUG):
            logging.debug(f"sending {WebSocketServer.msg_type_str(type)} websocket message " \
                f"(type={type} size={len(payload)})")

        # Ensure that JSON-style payload is converted to a string
        if type == WebSocketServer.MESSAGE_JSON and not isinstance(payload, str):
            payload = json.dumps(payload)

        # Encode the payload as bytes
        if not isinstance(payload, bytes):
            if encoding is not None:
                payload = bytes(payload, encoding=encoding)
            else:
                payload = bytes(payload)

        print(f"PAYLOAD: {payload}")

        # Send the message
        try:
            self.websocket.send(b''.join([

                # 32-byte message header format:
                #
                #   0   uint64  message_id    (message_count_tx)
                #   8   uint64  timestamp     (milliseconds since Unix epoch)
                #   16  uint16  magic_number  (42)
                #   18  uint16  message_type  (0=json, 1=text, >=2 binary)
                #   20  uint32  payload_size  (in bytes)
                #   24  uint32  unused        (padding)
                #   28  uint32  unused        (padding)
                struct.pack('!QQHHIII',
                    self.msg_count_tx,
                    int(timestamp),
                    42, 
                    type,
                    len(payload),
                    0, 
                    0,
                ),
                payload
            ]))
            self.msg_count_tx += 1
        except Exception as err:
            logging.warning(f"failed to send websocket message to client ({err})")

if __name__ == "__main__":

    # Define a test callback that just prints messages
    def callback(msg, *args, **kwargs):
        print(f"Callback: {msg}")

    # Set logging
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Start the server
    wss = WebSocketServer(
        host="localhost",
        port=49000,
        msg_callback=callback,
    )
    wss.start()