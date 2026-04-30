from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = ""
    user: str = "Unknown"
    channel: str = "warroom"


class MfaSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = ""
    task_id: str = "default"


class McpCallbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = ""
    action: str = ""


class ForgeDiscoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = ""


class ForgeDispatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = ""
    prompt: str = ""


class UcpDiscoverRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant_url: str = ""


class UcpSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant_url: str = ""
    query: str = ""


class UcpTransactRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    merchant_url: str = ""
    product_id: str = ""
    params: dict = Field(default_factory=dict)


class UcpConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: str = ""


class GoogleOauthStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str = ""
    client_secret: str = ""


async def parse_request_model_or_error(
    request: Request,
    model_cls,
    request_id: str,
    *,
    error_response,
):
    try:
        payload = await request.json()
        return model_cls.model_validate(payload)
    except ValidationError:
        return error_response(422, "Invalid request body.", request_id=request_id)
    except Exception:
        return error_response(422, "Request body must be valid JSON.", request_id=request_id)
