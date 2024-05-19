import logging
import ssl
import threading

from websockets.sync.server import serve as websocket_serve
from websockets.exceptions import ConnectionClosed

class WebSocketServer():

    def __init__(
        self, 
        host='0.0.0.0',
        port=49000,
        ssl_cert=None, 
        ssl_key=None,
        msg_callback=None, 
        **kwargs
    ):

        # Save parameters
        self.host = host
        self.port = port
        self.ssl_key = ssl_key
        self.ssl_cert = ssl_cert
        self.msg_callback = msg_callback

        # Configure SSL
        self.ssl_context = None
        if self.ssl_cert and self.ssl_key:
            self.ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            self.ssl_context.load_cert_chain(certfile=self.ssl_cert, keyfile=self.ssl_key)

        # Set up websocket server
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
        self.websocket = websocket  # TODO handle multiple clients
        remote_address = websocket.remote_address

        logging.info(f"new websocket connection from {remote_address}")

        '''
        # empty the queue from before the connection was made
        # (otherwise client will be flooded with old messages)
        # TODO implement self.connected so the ws_queue doesn't grow so large without webclient connected...
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

        # TODO: figure out and test the following:

        # header_size = 32
            
        # while True:
        #     msg = websocket.recv()
            
        #     if isinstance(msg, str):
        #         logging.warning(f'dropping text-mode websocket message from {websocket.remote_address} "{msg}"')
        #         continue
                
        #     if len(msg) <= header_size:
        #         logging.warning(f"dropping invalid websocket message from {websocket.remote_address} (size={len(msg)})")
        #         continue
                
        #     msg_id, timestamp, magic_number, msg_type, payload_size = \
        #         struct.unpack_from('!QQHHI', msg)
            
        #     metadata = msg[24:32].split(b'\x00')[0].decode()
            
        #     if magic_number != 42:
        #         logging.warning(f"dropping invalid websocket message from {websocket.remote_address} (magic_number={magic_number} size={len(msg)})")
        #         continue

        #     if msg_id != self.msg_count_rx:
        #         logging.debug(f"recieved websocket message from {websocket.remote_address} with out-of-order ID {msg_id}  (last={self.msg_count_rx})")
        #         self.msg_count_rx = msg_id
                
        #     self.msg_count_rx += 1
        #     msgPayloadSize = len(msg) - header_size
            
        #     if payload_size != msgPayloadSize:
        #         logging.warning(f"recieved invalid websocket message from {websocket.remote_address} (payload_size={payload_size} actual={msgPayloadSize}");
            
        #     payload = msg[header_size:]
            
        #     if msg_type == WebServer.MESSAGE_JSON:  # json
        #         payload = json.loads(payload)
        #     elif msg_type == WebServer.MESSAGE_TEXT:  # text
        #         payload = payload.decode('utf-8')

        #     if self.trace and logging.getLogger().isEnabledFor(logging.DEBUG):
        #         logging.debug(f"recieved {WebServer.msg_type_str(msg_type)} websocket message from {websocket.remote_address} (type={msg_type} size={payload_size})")
        #         if msg_type <= WebServer.MESSAGE_TEXT:
        #             pprint.pprint(payload)
                
        #     # save uploaded files/images to the upload dir
        #     filename = None
                
        #     if self.upload_dir and metadata and (msg_type == WebServer.MESSAGE_FILE or msg_type == WebServer.MESSAGE_IMAGE):
        #         filename = f"{datetime.datetime.utcfromtimestamp(timestamp/1000).strftime('%Y%m%d_%H%M%S')}.{metadata}"
        #         filename = os.path.join(self.upload_dir, filename)
        #         threading.Thread(target=self.save_upload, args=[payload, filename]).start()
             
        #     # decode images in-memory
        #     if msg_type == WebServer.MESSAGE_IMAGE:
        #         try:
        #             payload = PIL.Image.open(io.BytesIO(payload))
        #             if filename:
        #                 payload.filename = filename
        #         except Exception as err:
        #             print(err)
        #             logging.error(f"failed to load invalid/corrupted {metadata} image uploaded from client")
                    
        #     self.on_message(payload, payload_size=payload_size, msg_type=msg_type, msg_id=msg_id, metadata=metadata, timestamp=timestamp, path=filename)

if __name__ == "__main__":
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    wss = WebSocketServer()
    wss.start()