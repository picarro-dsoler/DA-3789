import os
import base64
import mimetypes
import requests
import msal
from dotenv import load_dotenv
from typing import Optional, Union, List, Dict

load_dotenv(override=True)
MICROSOFT_CLIENT_ID = os.getenv("MICROSOFTCLIENTID")
MICROSOFT_TENANT_ID = os.getenv("MICROSOFTTENANTID")
MICROSOFT_CLIENT_SECRET = os.getenv("MICROSOFTCLIENTSECRET")

CHUNK_SIZE = 327680  # 320 KB
SMALL_THRESHOLD = 3 * 1024 * 1024  # 3 MB
GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]





def _get_token():
    app = msal.ConfidentialClientApplication(
        MICROSOFT_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{MICROSOFT_TENANT_ID}",
        client_credential=MICROSOFT_CLIENT_SECRET,
    )
    tok = app.acquire_token_for_client(scopes=GRAPH_SCOPE)
    token = tok.get("access_token")
    if not token:
        raise Exception(f"Token error: {tok.get('error_description')}")
    return token


def _create_draft(sender, token, subject, body, recipient):
    url = f"https://graph.microsoft.com/v1.0/users/{sender}/messages"
    message = {
        "subject": subject,
        "body": {"contentType": "Text", "content": body},
        "toRecipients": [{"emailAddress": {"address": recipient}}],
    }
    r = requests.post(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json=message)
    r.raise_for_status()
    return r.json()["id"]


def _attach_small_file(sender, token, message_id, path, name=None, content_type=None):
    name = name or os.path.basename(path)
    if content_type is None:
        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode("utf-8")

    url = f"https://graph.microsoft.com/v1.0/users/{sender}/messages/{message_id}/attachments"
    payload = {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": name,
        "contentType": content_type,
        "contentBytes": content_b64,
    }
    r = requests.post(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json=payload)
    r.raise_for_status()


def _attach_large_file(sender, token, message_id, path, name=None, content_type=None):
    name = name or os.path.basename(path)
    if content_type is None:
        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    size = os.path.getsize(path)

    # Create upload session
    url = f"https://graph.microsoft.com/v1.0/users/{sender}/messages/{message_id}/attachments/createUploadSession"
    payload = {"AttachmentItem": {"attachmentType": "file", "name": name, "size": size, "contentType": content_type}}
    r = requests.post(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, json=payload)
    r.raise_for_status()
    upload_url = r.json()["uploadUrl"]

    # Chunked upload
    with open(path, "rb") as f:
        sent = 0
        while sent < size:
            chunk = f.read(CHUNK_SIZE)
            start = sent
            end = sent + len(chunk) - 1
            rr = requests.put(upload_url, headers={"Content-Range": f"bytes {start}-{end}/{size}"}, data=chunk)
            rr.raise_for_status()
            sent += len(chunk)


def _attach_auto(sender, token, message_id, path, name=None, content_type=None):
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    if os.path.getsize(path) <= SMALL_THRESHOLD:
        _attach_small_file(sender, token, message_id, path, name, content_type)
    else:
        _attach_large_file(sender, token, message_id, path, name, content_type)


def _send_message(sender, token, message_id):
    url = f"https://graph.microsoft.com/v1.0/users/{sender}/messages/{message_id}/send"
    r = requests.post(url, headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    return r.status_code
    

def send_email_via_outlook_api(
    file_path: Optional[str] = None,
    file_name: Optional[str] = None,
    subject: str = "",
    body: str = "",
    recipient: str = "",
    attachments: Optional[Union[str, List[str], List[Dict[str, str]]]] = None,
    sender: str = "gasanalytics@picarro.com",
) -> int:
    """
    Sends an email via Microsoft Graph API with zero, one, or multiple attachments.

    Args:
        file_path (str or None): Directory path of the file to attach. If None, no attachment is sent.
        file_name (str or None): Name of the file to attach. If None, no attachment is sent.
        subject (str): Email subject.
        body (str): Email body content.
        recipient (str): Email address of the recipient.
        attachments (str or list[str] or list[dict[str, str]] or None):
            - str: single file path
            - list[str]: list of file paths
            - list[dict]: [{"path": "...", "name": "...", "content_type": "..."}]
            If None, falls back to file_path/file_name for backward compatibility.
        sender (str): Email address of the sender (must be authorized in Graph).

    Returns:
        int: HTTP status code from the send message request.
    """
    try:
        token = _get_token()
        msg_id = _create_draft(sender, token, subject, body, recipient)

        # Build a normalized list of attachments
        attach_list: List[Dict[str, Optional[str]]] = []

        # Backward-compat single file args
        if attachments is None and file_path and file_name:
            single = os.path.join(file_path, file_name)
            attach_list = [{"path": single}]
        else:
            if isinstance(attachments, str):
                attach_list = [{"path": attachments}]
            elif isinstance(attachments, list):
                if attachments and isinstance(attachments[0], dict):
                    attach_list = attachments
                else:
                    attach_list = [{"path": p} for p in attachments or []]
            else:
                attach_list = []

        # Attach all
        for a in attach_list:
            path = a["path"]
            name = a.get("name")
            ctype = a.get("content_type")
            _attach_auto(sender, token, msg_id, path, name, ctype)

        # Send
        return _send_message(sender, token, msg_id)

    except requests.exceptions.HTTPError as e:
        raise RuntimeError(f"HTTP error: {e.response.status_code} - {e.response.text}") from e
    except Exception as e:
        raise RuntimeError(f"Error: {e}") from e
