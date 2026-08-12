import json
import urllib.request
from db.misc import get_notify_config
from core.utils import DASHBOARD_VERSION

_NOTIFY_TIMEOUT = 5
_USER_AGENT = f"EshuGateway/{DASHBOARD_VERSION}"


def _is_discord_native(url: str) -> bool:
    """True for Discord's native webhook endpoint. Discord only understands the
    Slack-style `{"text": ...}` body on its /slack-suffixed URL; the plain
    https://discord.com/api/webhooks/<id>/<token> endpoint requires `content`."""
    return ('discord.com' in url) and not url.rstrip('/').endswith('/slack')


def _build_payload(title: str, body: str, dashboard_url: str = '', url: str = ''):
    # Discord uses Markdown: bold is **text**, links are [text](url). Slack /
    # Mattermost / Discord /slack use *text* for bold and <url|text> for links.
    if _is_discord_native(url):
        text = f"**{title}**\n{body}"
        if dashboard_url:
            text += f"\n🔗 [Open dashboard]({dashboard_url})"
        return {"content": text}
    text = f"*{title}*\n{body}"
    if dashboard_url:
        text += f"\n🔗 <{dashboard_url}|Open dashboard>"
    return {"text": text}


def send_notify(event_type: str, title: str, body: str):
    """Send an external webhook notification. Returns True if delivered, False
    otherwise (no webhook configured, event not subscribed, or the POST failed).

    The 'test' event always sends (used by the dashboard's Test button) so the
    operator can validate the webhook regardless of subscribed events."""
    config = get_notify_config()
    if not config['url']:
        return False
    if event_type != 'test':
        allowed = [e.strip() for e in config['events'].split(',') if e.strip()]
        if event_type not in allowed:
            return False
    payload = _build_payload(title, body, config.get('dashboard_url', ''), config['url'])
    return _do_webhook(config['url'], payload)


def _do_webhook(url: str, payload: dict) -> bool:
    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data,
                                     headers={'Content-Type': 'application/json',
                                              'User-Agent': _USER_AGENT},
                                     method='POST')
        with urllib.request.urlopen(req, timeout=_NOTIFY_TIMEOUT) as resp:
            ok = 200 <= resp.status < 300
            if not ok:
                print(f"[notify] webhook returned HTTP {resp.status}", flush=True)
            return ok
    except Exception as e:
        # Discord rejects the default Python-urllib UA with 403 — we set a
        # real UA, but surface any residual failure instead of swallowing it.
        print(f"[notify] webhook failed: {type(e).__name__}: {e}", flush=True)
        return False
