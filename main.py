from datetime import datetime
import json
import secrets
import time

from starlette.responses import JSONResponse
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import RedirectResponse
from fastmcp import FastMCP
from fastmcp.server.auth.auth import OAuthProvider
from fulcra_api.core import FulcraAPI
from pydantic_settings import BaseSettings
from pydantic import AnyHttpUrl
import structlog
import uvicorn

OIDC_SCOPES = ["openid", "profile", "name", "email"]
# XXX XXX XXX parameterize this
SERVER_URL = "http://localhost:4499"

class Settings(BaseSettings):
    oidc_server_url: str = SERVER_URL
    fulcra_environment: str = "localdev"
    port: int = 8080
    oidc_client_id: str = "tc92NeNkAg748rlxBbm79cKdG9AOAbfc"        # XXX XXX XXX XXX XXX

settings = Settings()

logger = structlog.getLogger(__name__)

class FulcraOAuthProvider(OAuthProvider):
    def __init__(
        self,
        issuer_url: AnyHttpUrl | str,
        service_documentation_url: AnyHttpUrl | str | None = None,
        client_registration_options: ClientRegistrationOptions | None = None,
        revocation_options: RevocationOptions | None = None,
        required_scopes: list[str] | None = None,
    ):
        super().__init__(
                issuer_url=issuer_url,
                service_documentation_url=service_documentation_url,
                client_registration_options=client_registration_options,
                revocation_options=revocation_options,
                required_scopes=required_scopes,
        )
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.auth_codes: dict[str, AuthorizationCode] = {}
        self.tokens: dict[str, AccessToken] = {}
        self.state_mapping: dict[str, dict[str, str]] = {}
        self.token_mapping: dict[str, str] = {}

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        """Get OAuth client information."""
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull):
        """Register a new OAuth client."""
        self.clients[client_info.client_id] = client_info

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        state = params.state or secrets.token_hex(16)
        self.state_mapping[state] = {
            "redirect_uri": str(params.redirect_uri),
            "code_challenge": params.code_challenge,
            "redirect_uri_provided_explicitly": str(
                params.redirect_uri_provided_explicitly
            ),
            "client_id": client.client_id,
        }
        fulcra = FulcraAPI(
            oidc_client_id=settings.oidc_client_id,
        )
        auth_url = fulcra.get_authorization_code_url(
                redirect_uri=f"{settings.oidc_server_url}/callback",              # XXX XXX XXX
                state=state,
        )
        return auth_url

    async def handle_callback(self, code: str, state: str) -> str:
        state_data = self.state_mapping.get(state)
        if not state_data:
            raise HTTPException(400, "Invalid state parameter")

        redirect_uri = state_data["redirect_uri"]
        code_challenge = state_data["code_challenge"]
        redirect_uri_provided_explicitly = (
            state_data["redirect_uri_provided_explicitly"] == "True"
        )
        client_id = state_data["client_id"]

        fulcra = FulcraAPI(
            oidc_client_id=settings.oidc_client_id,
        )
        try:
            fulcra.authorize_with_authorization_code(
                code=code,
                redirect_uri=f"{settings.oidc_server_url}/callback",
            )
            access_token = fulcra.get_cached_access_token()
            new_code = f"mcp_{secrets.token_hex(16)}"
            # Create MCP authorization code
            auth_code = AuthorizationCode(
                code=new_code,
                client_id=client_id,
                redirect_uri=AnyHttpUrl(redirect_uri),
                redirect_uri_provided_explicitly=redirect_uri_provided_explicitly,
                expires_at=time.time() + 300,
                scopes=OIDC_SCOPES,
                code_challenge=code_challenge,
            )
            self.auth_codes[new_code] = auth_code
            self.tokens[access_token] = AccessToken(
                token=access_token,
                client_id=client_id,
                scopes=OIDC_SCOPES,
                expires_at=None,
            )
        except Exception as e:
            logger.error("oauth2 code exchange failure", exc_info=e)
            raise HTTPException(400, "failed to exchange code for token")

        del self.state_mapping[state]
        return construct_redirect_uri(redirect_uri, code=new_code, state=state)


    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        """Load an authorization code."""
        return self.auth_codes.get(authorization_code)


    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if authorization_code.code not in self.auth_codes:
            raise ValueError("Invalid authorization code")

        # Generate MCP access token
        mcp_token = f"mcp_{secrets.token_hex(32)}"

        # Store MCP token
        self.tokens[mcp_token] = AccessToken(
            token=mcp_token,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=int(time.time()) + 3600,
        )

        # Find GitHub token for this client
        oidc_token = next(
            (
                token
                for token, data in self.tokens.items()
                # see https://github.blog/engineering/platform-security/behind-githubs-new-authentication-token-formats/
                # which you get depends on your GH app setup.
                if data.client_id == client.client_id
            ),
            None,
        )

        if oidc_token:
            self.token_mapping[mcp_token] = oidc_token

        del self.auth_codes[authorization_code.code]

        return OAuthToken(
            access_token=mcp_token,
            token_type="bearer",
            expires_in=3600,
            scope=" ".join(authorization_code.scopes),
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Load and validate an access token."""
        access_token = self.tokens.get(token)
        if not access_token:
            return None

        # Check if expired
        if access_token.expires_at and access_token.expires_at < time.time():
            del self.tokens[token]
            return None

        return access_token

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        """Load a refresh token - not supported."""
        return None

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        """Exchange refresh token"""
        raise NotImplementedError("Not supported")

    async def revoke_token(
        self, token: str, token_type_hint: str | None = None
    ) -> None:
        """Revoke a token."""
        if token in self.tokens:
            del self.tokens[token]

oauth_provider = FulcraOAuthProvider(
      issuer_url=AnyHttpUrl(settings.oidc_server_url),
      client_registration_options=ClientRegistrationOptions(
          enabled=True,
          valid_scopes=OIDC_SCOPES,
          default_scopes=OIDC_SCOPES,
          ),
      required_scopes=["openid"],
)
mcp = FastMCP(
    name="Fulcra Context Agent",
    instructions="""
    This server provides personal data retrieval tools.
    Always specify the time zone when using times as parameters.
    """,
    auth=oauth_provider,
)

def get_fulcra_object() -> FulcraAPI:
    mcp_access_token = get_access_token()
    if not mcp_access_token:
        raise HTTPException(401, "Not authenticated")
    fulcra_token = oauth_provider.token_mapping.get(mcp_access_token.token)
    if fulcra_token is None:
        raise HTTPException(401, "Not authenticated")
    fulcra = FulcraAPI()
    fulcra.set_cached_access_token(fulcra_token)
    return fulcra

@mcp.tool()
async def get_workouts(
        start_time: datetime, 
        end_time: datetime
) -> str:
    """Get details about the workouts that the user has done during a period of time.
    Result timestamps will include time zones. Always translate timestamps to the user's local
    time zone when this is known.

    Args:
        start_time: The starting time of the period. Must include tz (ISO8601).
        end_time: the ending time of the period. Must include tz (ISO8601).
    """
    fulcra = get_fulcra_object()
    workouts = fulcra.apple_workouts(start_time, end_time)
    return f"Workouts during {start_time} and {end_time}: " + json.dumps(workouts)


@mcp.tool()
async def get_metrics_catalog() -> str:
    """Get the catalog of available metrics that can be used in time-series API calls
    (`metric_time_series` and `metric_samples`).
    """
    fulcra = get_fulcra_object()
    catalog = fulcra.metrics_catalog()
    return "Available metrics: " + json.dumps(catalog)


@mcp.tool()
async def get_metric_time_series(
    metric_name: str,
    start_time: datetime,
    end_time: datetime,
    sample_rate: float | None = 60.0,
    replace_nulls: bool | None = False,
    calculations: list[str] | None = None,
) -> str:
    """Get user's time-series data for a single Fulcra metric.

    Covers the time starting at start_time (inclusive) until end_time (exclusive).
    Result timestamps will include tz. Always translate timestamps to the user's local
    tz when this is known.

    Args:
        metric_name: The name of the time-series metric to retrieve. Use `get_metrics_catalog` to find available metrics.
        start_time: The starting time period (inclusive). Must include tz (ISO8601).
        end_time: The ending time (exclusive). Must include tz (ISO8601).
        sample_rate: Optional. The number of seconds per sample. Default is 60. Can be smaller than 1.
        replace_nulls: Optional. When true, replace all NA with 0. Default is False.
        calculations: Optional. A list of additional calculations to perform for each
        time slice.  Not supported on cumulative metrics.  Options: "max", "min", "delta", "mean", "uniques", "allpoints", "rollingmean".
    Returns:
        A JSON string representing a list of data points for the metric.
        For time ranges where data is missing, the values will be NA unless replace_nulls is true.
    """
    fulcra = get_fulcra_object()
    # Ensure defaults are passed correctly if None
    kwargs = {}
    if sample_rate is not None:
        kwargs["sample_rate"] = sample_rate
    if replace_nulls is not None:
        kwargs["replace_nulls"] = replace_nulls
    if calculations is not None:
        kwargs["calculations"] = calculations

    time_series_df = fulcra.metric_time_series(
        metric=metric_name,
        start_time=start_time,
        end_time=end_time,
        **kwargs,
    )
    # Convert DataFrame to JSON. `orient='records'` gives a list of dicts.
    # `date_format='iso'` ensures datetimes are ISO8601 strings.
    # `default_handler=str` can help with any other non-serializable types, though less likely with typical DataFrame content.
    return f"Time series data for {metric_name} from {start_time} to {end_time}: " + time_series_df.to_json(orient="records", date_format="iso", default_handler=str)


mcp_asgi_app = mcp.http_app(path="/")
app = FastAPI(lifespan=mcp_asgi_app.lifespan, debug=True)

@app.get("/callback")
async def callback_handler(request: Request) -> Response:
    code = request.query_params.get("code")
    state = request.query_params.get("state")

    if not code or not state:
        raise HTTPException(400, "Missing code or state parameter")

    try:
        redirect_uri = await oauth_provider.handle_callback(code, state)
        return RedirectResponse(status_code=302, url=redirect_uri)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Unexpected error", exc_info=e)
    raise HTTPException(500, "Unexpected error")


app.mount("/", mcp_asgi_app)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=settings.port)
