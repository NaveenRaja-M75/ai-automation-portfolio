#!/usr/bin/env python3
"""
QUEUE — today's WhatsApp and Instagram messages as one tap-through page.

WHY NEITHER CHANNEL IS SENT BY A SCRIPT
    WhatsApp: the only ways to send from code are unofficial libraries that
    get the number banned, or the Business API, which needs Meta verification
    and charges per conversation. Instagram: sending a DM from code needs your
    login session cookies, which is full control of the account, and Meta's
    own API forbids starting a conversation with someone who has not messaged
    you. Both were rejected.

    What is left is one tap away from automation. WhatsApp's click-to-chat
    link opens the chat with the message already typed. Instagram cannot
    pre-fill, so its button copies the message first, then opens the DM, and
    you paste.

WHO IS ON THE PAGE
    Only leads whose next touch (see cadence.py) is on one of these two
    channels AND is due today or earlier. Step 1 uses the draft already
    written and approved in the note. Later steps use the nudges below, which
    refer back to the earlier message instead of repeating it.

AFTER YOU SEND
    python run.py done whatsapp            stamps every queued WhatsApp lead
    python run.py done instagram --skip "Digises, Klasse"
    Without this the same people come back tomorrow.

USAGE
    python run.py queue                    both channels
    python run.py whatsapp                 WhatsApp only
    python run.py instagram                Instagram only
    add --now to ignore timing
"""
import sys
import json
import html
import argparse
import datetime
import urllib.parse
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vault as V
import cadence as C
from dispatch import lint, extract_drafts
from draft_all import greeting_name

OUT = V.ROOT / "queue.html"
QUEUE_STATE = V.STATE / "queue.json"

# ---------------------------------------------------------------- messages
# Keyed by (audience, channel, kind). kind: nudge (step 2) or final (step 3).
# Step 1 comes from the approved draft in the note itself.
T = {
    ("agency", "whatsapp", "nudge"): (
        "Hey {name}! Naveen here from Monarc Studio, I sent you a mail a few "
        "days back so this is just a quick nudge.\n\n"
        "Short version: when your client asks for an ad video, we produce it "
        "under your agency's name using AI, one flat price per video, so you "
        "know your cost before you quote.\n\n"
        "If it helps, I can put together a short sample using one of your "
        "current clients' products so you can see the finish before deciding "
        "anything."),
    ("agency", "instagram", "nudge"): (
        "Hey {name}! Naveen from Monarc Studio here, I sent you a mail a few "
        "days back. We make ad videos for agency clients using AI, under your "
        "agency's name, one flat price per video.\n\n"
        "Happy to put together a short sample using one of your clients' "
        "products if you'd like to see it first!"),
    ("agency", "instagram", "final"): (
        "Hey {name}! Naveen from Monarc Studio. I reached out on mail and "
        "WhatsApp last week about making AI ad videos for your clients under "
        "your agency's name. Don't want to keep chasing, so this is my last "
        "note.\n\n"
        "If a client ever asks for a video and you want a flat quote, just "
        "reply here."),
    ("agency", "whatsapp", "final"): (
        "Hey {name}! Naveen from Monarc Studio, last note from me. If a client "
        "ever asks for an ad video and you want a flat quote under your "
        "agency's name, just reply here."),

    ("brand", "whatsapp", "nudge"): (
        "Hey {name}! Naveen here from Monarc Studio, I sent you a mail a few "
        "days back so this is just a quick nudge.\n\n"
        "We make ad videos for brands using AI, product commercials and UGC "
        "style, one flat price per video, so new creatives don't need a new "
        "shoot every time.\n\n"
        "Happy to make a short sample with one of your products so you can see "
        "it before deciding anything."),
    ("brand", "instagram", "nudge"): (
        "Hey {name}! Naveen from Monarc Studio here, I sent you a mail a few "
        "days back. We make AI ad videos for brands, product commercials and "
        "UGC style, one flat price per video.\n\n"
        "Happy to make a short sample with one of your products if you'd like "
        "to see it first!"),
    ("brand", "instagram", "final"): (
        "Hey {name}! Naveen from Monarc Studio. I reached out on mail and "
        "WhatsApp last week about AI ad videos for your products. Don't want "
        "to keep chasing, so this is my last note.\n\n"
        "If you ever need new ad creatives at a flat price, just reply here."),
    ("brand", "whatsapp", "final"): (
        "Hey {name}! Naveen from Monarc Studio, last note from me. If you ever "
        "need new ad videos at a flat price per video, just reply here."),
}


def audience(fm):
    return "agency" if fm.get("lead_type") == "agency" else "brand"


def message_for(fm, body, nt):
    name = greeting_name(fm)
    ch = nt["channel"]
    if nt["step"] == 1:
        return extract_drafts(body).get(ch, ""), "first contact"
    kind = "final" if nt["step"] >= 3 else "nudge"
    # a single-route lead repeating its only channel gets the nudge wording
    text = T[(audience(fm), ch, kind)].format(name=name)
    label = {"nudge": "follow-up", "final": "last touch"}[kind]
    return text, f"{label}, after {' + '.join(nt['prior'])}"


# ------------------------------------------------------------------ collect
def collect(channels, force_now):
    rows, skipped, numbers = [], [], {}
    candidates = [(p, "sent") for p in V.notes_in("sent")]
    candidates += [(p, "approved") for p in V.approved_notes()]

    for path, stage in candidates:
        fm, body = V.read_note(path)
        brand = fm.get("brand", path.stem)
        nt = C.next_touch(fm)
        if nt is None:
            if C.replied(fm):
                skipped.append((brand, "replied, handle it in the thread"))
            continue
        if nt["channel"] not in channels:
            continue
        if nt["overdue_days"] < 0 and not force_now:
            skipped.append((brand, f"{nt['channel']} due in "
                                   f"{-nt['overdue_days']}d ({nt['due']})"))
            continue

        text, why = message_for(fm, body, nt)
        if len(text) < 25:
            skipped.append((brand, f"no {nt['channel']} draft in the note"))
            continue
        problems = lint(text)
        if problems:
            skipped.append((brand, "BLOCKED: " + "; ".join(problems[:2])))
            continue

        if nt["channel"] == "whatsapp":
            num, _ = C.mobile(fm.get("phone"))
            if num in numbers:
                skipped.append((brand, f"same number as {numbers[num]}"))
                continue
            numbers[num] = brand
            link = f"https://wa.me/{num}?text=" + urllib.parse.quote(text)
            contact = "+" + num
        else:
            handle = str(fm.get("instagram", "")).strip().lstrip("@")
            link = f"https://ig.me/m/{urllib.parse.quote(handle)}"
            contact = "@" + handle

        rows.append({"path": str(path), "stage": stage, "brand": brand,
                     "channel": nt["channel"], "text": text, "why": why,
                     "contact": contact, "link": link,
                     "city": fm.get("city") or fm.get("country") or ""})
    rows.sort(key=lambda r: (r["channel"], r["brand"].lower()))
    return rows, skipped


# --------------------------------------------------------------------- page
CSS = """
:root{--bg:#faf9f7;--card:#fff;--line:#e6e3de;--ink:#1a1a1a;--mute:#858585;
  --msg:#f4f2ee;--msgink:#4d4d4d}
@media(prefers-color-scheme:dark){:root{--bg:#16181a;--card:#1f2225;
  --line:#2f3336;--ink:#e8e6e3;--mute:#8d8d8d;--msg:#121416;--msgink:#b8b5b0}}
body{font:16px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;margin:0 auto;
  padding:20px 16px 60px;max-width:640px;background:var(--bg);color:var(--ink)}
h1{font-size:19px;margin:0 0 4px} h2{font-size:14px;margin:26px 0 10px;
  text-transform:uppercase;letter-spacing:.06em;color:var(--mute)}
.sub{color:var(--mute);font-size:13px;margin:0 0 8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:14px 15px;margin-bottom:12px}
.top{display:flex;justify-content:space-between;align-items:baseline;gap:10px}
.b{font-weight:600;font-size:15px} .why{font-size:12px;color:var(--mute)}
.meta{font-size:12.5px;color:var(--mute);margin:2px 0 10px}
.msg{background:var(--msg);color:var(--msgink);border-radius:8px;
  padding:10px 11px;font-size:13px;white-space:pre-wrap;max-height:78px;
  overflow:hidden;cursor:pointer} .msg.open{max-height:none}
.go{display:block;width:100%;box-sizing:border-box;text-align:center;
  color:#fff;text-decoration:none;font:600 15px/1 inherit;padding:12px;
  border:0;border-radius:9px;margin-top:11px;cursor:pointer}
.wa{background:#1f9d55} .ig{background:#c13584}
.done .go{background:#9a9a9a} .done .b{text-decoration:line-through;color:var(--mute)}
.hint{font-size:12px;color:var(--mute);margin-top:6px;text-align:center}
.skip{font-size:13px;color:var(--mute);border-top:1px solid var(--line);
  margin-top:28px;padding-top:16px} .skip div{margin-bottom:4px}
"""

JS = """
function copyText(t){
  if(navigator.clipboard&&window.isSecureContext){
    return navigator.clipboard.writeText(t).catch(function(){fallback(t)});
  }
  fallback(t); return Promise.resolve();
}
function fallback(t){
  var a=document.createElement('textarea');a.value=t;a.style.position='fixed';
  a.style.opacity='0';document.body.appendChild(a);a.select();
  try{document.execCommand('copy')}catch(e){} document.body.removeChild(a);
}
function mark(c){
  try{localStorage.setItem('monarc_q_'+c.dataset.key,'1')}catch(e){}
  c.classList.add('done');
}
document.querySelectorAll('.card').forEach(function(c){
  try{if(localStorage.getItem('monarc_q_'+c.dataset.key))c.classList.add('done')}catch(e){}
  var m=c.querySelector('.msg');
  m.addEventListener('click',function(){m.classList.toggle('open')});
  var btn=c.querySelector('.go');
  btn.addEventListener('click',function(ev){
    if(c.dataset.ch==='instagram'){
      ev.preventDefault();
      var text=c.querySelector('.msg').textContent, url=btn.dataset.href;
      copyText(text).then(function(){
        btn.textContent='Copied, opening Instagram...';
        mark(c); setTimeout(function(){window.open(url,'_blank')},350);
      });
    } else { mark(c); }
  });
});
"""


def card(r):
    key = html.escape(f"{r['channel']}_{Path(r['path']).stem}_{datetime.date.today()}")
    esc = html.escape
    if r["channel"] == "whatsapp":
        btn = (f'<a class="go wa" href="{esc(r["link"], quote=True)}" '
               f'target="_blank" rel="noopener">Open WhatsApp</a>')
        hint = ""
    else:
        btn = (f'<button class="go ig" data-href="{esc(r["link"], quote=True)}">'
               f'Copy message + open Instagram</button>')
        hint = '<div class="hint">In the chat, long-press the box and Paste</div>'
    return (f'<div class="card" data-key="{key}" data-ch="{r["channel"]}">'
            f'<div class="top"><span class="b">{esc(r["brand"][:44])}</span>'
            f'<span class="why">{esc(r["why"])}</span></div>'
            f'<div class="meta">{esc(r["contact"])} &middot; {esc(str(r["city"]))}</div>'
            f'<div class="msg">{esc(r["text"])}</div>{btn}{hint}</div>')


def build(rows, skipped):
    wa = [r for r in rows if r["channel"] == "whatsapp"]
    ig = [r for r in rows if r["channel"] == "instagram"]
    out = ['<!doctype html><html><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width,initial-scale=1">',
           f'<title>Monarc outreach queue</title><style>{CSS}</style></head><body>',
           '<h1>Outreach queue</h1>',
           f'<p class="sub">{len(wa)} WhatsApp &middot; {len(ig)} Instagram &middot; '
           f'built {datetime.date.today():%a %d %b}. Tap a grey box to read the '
           f'full message. Leave 2 to 3 minutes between sends.</p>']
    if wa:
        out.append("<h2>WhatsApp</h2>")
        out += [card(r) for r in wa]
    if ig:
        out.append("<h2>Instagram</h2>")
        out += [card(r) for r in ig]
    if skipped:
        out.append('<div class="skip"><b>Not on today\'s page</b>')
        out += [f"<div>{html.escape(b[:44])} &mdash; {html.escape(w)}</div>"
                for b, w in skipped]
        out.append("</div>")
    out.append(f"<script>{JS}</script></body></html>")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="all",
                    choices=["all", "whatsapp", "instagram"])
    ap.add_argument("--now", action="store_true")
    ap.add_argument("--no-open", action="store_true")
    a = ap.parse_args()
    channels = (["whatsapp", "instagram"] if a.channel == "all"
                else [a.channel])

    rows, skipped = collect(channels, a.now)
    OUT.write_text(build(rows, skipped), encoding="utf-8")
    V.STATE.mkdir(parents=True, exist_ok=True)
    QUEUE_STATE.write_text(json.dumps(
        {"built": datetime.date.today().isoformat(),
         "items": [{k: r[k] for k in ("path", "stage", "brand", "channel")}
                   for r in rows]}, indent=1), encoding="utf-8")

    print(f"{len(rows)} ready, {len(skipped)} not included\n")
    for r in rows:
        print(f"  {r['channel']:<10} {r['brand'][:36]:<38} {r['contact']:<24} {r['why']}")
    if skipped:
        print()
        for b, w in skipped:
            print(f"  --  {b[:36]:<38} {w}")
    print(f"\nPage: {OUT}")
    if rows:
        print("After sending, run:  python run.py done whatsapp   "
              "(or instagram, add --skip \"Name, Name\" for any you did not send)")
        if not a.no_open:
            webbrowser.open(OUT.as_uri())


if __name__ == "__main__":
    main()
