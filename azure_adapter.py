import json
import os
import httpx

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse


# ============================================================
# CONFIGURATION
# ============================================================

# Your existing gemini-web2api server
GEMINI_API_URL = "http://127.0.0.1:8081"

# Port for this Azure-compatible adapter
ADAPTER_HOST = "127.0.0.1"
ADAPTER_PORT = 8082

# This is the fake Azure API key that Cursor will send.
# It does NOT need to be a real Azure key.
AZURE_API_KEY = "sk-gemini"

# Cursor's Azure Deployment Name.
# You can call it whatever you want.
DEPLOYMENT_NAME = "gemini-pro"


# ============================================================
# MODEL MAPPING
# ============================================================

# Cursor sends the deployment name.
#
# We translate it to the actual Gemini model understood
# by gemini-web2api.
#
# Change this if you want another Gemini model.
MODEL_MAPPING = {
    "gemini-pro": "gemini-3.1-pro",

    # Optional aliases:
    "gemini-flash": "gemini-3.6-flash",
    "gemini-thinking": "gemini-3.6-flash-thinking",
}


# ============================================================
# HELPERS
# ============================================================

def get_gemini_model(deployment_name):
    """
    Convert the Azure deployment name into a Gemini Web2API
    model name.
    """

    return MODEL_MAPPING.get(
        deployment_name,
        MODEL_MAPPING[DEPLOYMENT_NAME]
    )


def send_json(handler, status_code, data):
    """
    Send a normal JSON response.
    """

    body = json.dumps(data).encode("utf-8")

    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()

    handler.wfile.write(body)


def send_error(handler, status_code, message):
    """
    Send an OpenAI-style error.
    """

    send_json(
        handler,
        status_code,
        {
            "error": {
                "message": message,
                "type": "invalid_request_error",
            }
        },
    )


# ============================================================
# REQUEST HANDLER
# ============================================================

class AzureAdapterHandler(BaseHTTPRequestHandler):

    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        print(
            "[Azure Adapter]",
            format % args
        )

    # --------------------------------------------------------
    # GET
    # --------------------------------------------------------

    def do_GET(self):

        parsed = urlparse(self.path)

        # Azure/Cursor may check models.
        if parsed.path.endswith("/models"):

            models = []

            for deployment, model in MODEL_MAPPING.items():

                models.append(
                    {
                        "id": deployment,
                        "object": "model",
                        "owned_by": "gemini-web2api",
                    }
                )

            send_json(
                self,
                200,
                {
                    "object": "list",
                    "data": models,
                },
            )

            return

        send_error(
            self,
            404,
            "Endpoint not found",
        )

    # --------------------------------------------------------
    # POST
    # --------------------------------------------------------

    def do_POST(self):

        parsed = urlparse(self.path)

        print()
        print("==========================================")
        print("Incoming request")
        print("Path:", parsed.path)
        print("==========================================")

        # ----------------------------------------------------
        # Validate API key
        # ----------------------------------------------------

        api_key = self.headers.get("api-key")

        if not api_key:

            authorization = self.headers.get(
                "Authorization",
                ""
            )

            if authorization.startswith("Bearer "):
                api_key = authorization[7:]

        if api_key != AZURE_API_KEY:

            send_error(
                self,
                401,
                "Invalid API key",
            )

            return

        # ----------------------------------------------------
        # Extract deployment name
        # ----------------------------------------------------

        deployment_name = None

        parts = parsed.path.strip("/").split("/")

        #
        # Expected Azure-style URL:
        #
        # /openai/deployments/gemini-pro/chat/completions
        #

        try:

            if (
                len(parts) >= 4
                and parts[0] == "openai"
                and parts[1] == "deployments"
            ):

                deployment_name = parts[2]

        except Exception:

            pass

        # ----------------------------------------------------
        # Fallback
        # ----------------------------------------------------

        if not deployment_name:

            deployment_name = DEPLOYMENT_NAME

        # ----------------------------------------------------
        # Resolve Gemini model
        # ----------------------------------------------------

        gemini_model = get_gemini_model(
            deployment_name
        )

        print(
            "Azure deployment:",
            deployment_name
        )

        print(
            "Gemini model:",
            gemini_model
        )

        # ----------------------------------------------------
        # Read request body
        # ----------------------------------------------------

        try:

            content_length = int(
                self.headers.get(
                    "Content-Length",
                    "0"
                )
            )

            raw_body = self.rfile.read(
                content_length
            )

            request_data = json.loads(
                raw_body.decode("utf-8")
            )

        except Exception as e:

            send_error(
                self,
                400,
                f"Invalid JSON: {e}",
            )

            return

        # ----------------------------------------------------
        # Replace model
        # ----------------------------------------------------

        request_data["model"] = gemini_model

        # ----------------------------------------------------
        # Remove Azure/OpenAI fields that might cause problems
        # ----------------------------------------------------

        request_data.pop(
            "deployment",
            None
        )

        # ----------------------------------------------------
        # Forward to gemini-web2api
        # ----------------------------------------------------

        target_url = (
            GEMINI_API_URL
            + "/v1/chat/completions"
        )

        print(
            "Forwarding to:",
            target_url
        )

        print(
            "Model:",
            request_data.get("model")
        )

        # ----------------------------------------------------
        # Detect streaming
        # ----------------------------------------------------

        streaming = (
            request_data.get(
                "stream",
                False
            )
            is True
        )

        try:

            with httpx.Client(
                timeout=300.0
            ) as client:

                if streaming:

                    self.handle_streaming(
                        client,
                        target_url,
                        request_data
                    )

                else:

                    self.handle_normal(
                        client,
                        target_url,
                        request_data
                    )

        except Exception as e:

            print(
                "ERROR:",
                repr(e)
            )

            send_error(
                self,
                502,
                f"Gemini Web2API connection failed: {e}",
            )

    # --------------------------------------------------------
    # Normal response
    # --------------------------------------------------------

    def handle_normal(
        self,
        client,
        target_url,
        request_data
    ):

        response = client.post(
            target_url,
            json=request_data,
            headers={
                "Authorization":
                    f"Bearer {AZURE_API_KEY}",
                "Content-Type":
                    "application/json",
            },
        )

        print(
            "Gemini status:",
            response.status_code
        )

        self.send_response(
            response.status_code
        )

        self.send_header(
            "Content-Type",
            response.headers.get(
                "content-type",
                "application/json"
            )
        )

        self.send_header(
            "Content-Length",
            str(len(response.content))
        )

        self.end_headers()

        self.wfile.write(
            response.content
        )

    # --------------------------------------------------------
    # Streaming response
    # --------------------------------------------------------

    def handle_streaming(
        self,
        client,
        target_url,
        request_data
    ):

        with client.stream(
            "POST",
            target_url,
            json=request_data,
            headers={
                "Authorization":
                    f"Bearer {AZURE_API_KEY}",
                "Content-Type":
                    "application/json",
            },
        ) as response:

            print(
                "Gemini streaming status:",
                response.status_code
            )

            self.send_response(
                response.status_code
            )

            self.send_header(
                "Content-Type",
                "text/event-stream"
            )

            self.send_header(
                "Cache-Control",
                "no-cache"
            )

            self.send_header(
                "Connection",
                "keep-alive"
            )

            self.end_headers()

            for chunk in response.iter_bytes():

                if chunk:

                    try:

                        self.wfile.write(
                            chunk
                        )

                        self.wfile.flush()

                    except BrokenPipeError:

                        print(
                            "Client disconnected."
                        )

                        break


# ============================================================
# START SERVER
# ============================================================

def main():

    print()
    print("==========================================")
    print(" Gemini Web2API Azure Adapter")
    print("==========================================")
    print()
    print(
        f"Adapter: http://{ADAPTER_HOST}:{ADAPTER_PORT}"
    )
    print(
        f"Gemini API: {GEMINI_API_URL}"
    )
    print(
        f"Deployment: {DEPLOYMENT_NAME}"
    )
    print(
        f"Gemini model: {MODEL_MAPPING[DEPLOYMENT_NAME]}"
    )
    print()
    print(
        "Cursor Azure Base URL:"
    )
    print(
        f"http://{ADAPTER_HOST}:{ADAPTER_PORT}"
    )
    print()
    print("Press CTRL+C to stop.")
    print()

    server = ThreadingHTTPServer(
        (
            ADAPTER_HOST,
            ADAPTER_PORT
        ),
        AzureAdapterHandler
    )

    try:

        server.serve_forever()

    except KeyboardInterrupt:

        print()
        print("Stopping...")

    finally:

        server.server_close()


if __name__ == "__main__":
    main()