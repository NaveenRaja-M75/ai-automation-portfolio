#!/usr/bin/env python3
"""
Labelled posts for the LinkedIn buyer filter. Run by `python run.py audit`.

The post stream lives or dies on one call: is this person ASKING for AI ad work,
or SELLING it / looking for a job / recruiting for someone else? Sellers use the
same words as buyers, so every change to SEEKER or STRONG_ASK in li_lib.py must
keep all of these right. Add a line whenever a wrong call shows up in the
workbook's Skipped sheet or Hot posts sheet.

    python engine/li_selftest.py      prints every miss
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

BUYERS = [
    "Looking for an AI video editor who knows how to create videos for DTC brands. AI UGC. Email your portfolio at hiring@dtc-brand.example",
    "We make organic snacks and we need an AI commercial for our launch next month.",
    "Hiring: AI UGC creator for our skincare brand. Remote, paid per video.",
    "Looking for someone who can make AI product ads for our Shopify store. DM me.",
    "We're looking for an AI filmmaker to create a 60 second brand film. Budget ready.",
    "Can anyone recommend a good AI video agency? We need AI ads for Meta.",
    "Need an AI content creator for our D2C coffee brand, 10 videos a month.",
    "Our startup is hiring an AI video creator to produce launch ads. Freelance is fine.",
    "Seeking an AI creative producer for a product launch campaign in Dubai.",
    "Who knows a team that makes AI UGC ads? Our agency needs white-label help for clients.",
    "We need someone to create AI generated video ads for our fashion label.",
    "In search of an AI commercial director for our fintech app launch.",
    "Looking for 2 AI UGC creators for our supplement brand. Paid per video, ongoing.",
    "My friend is hiring an AI video editor for UGC ads, remote. Comment and I'll connect you.",
    "We're a jewellery brand looking for AI product videos for Instagram.",
    "Hiring a freelance AI ads creator for our ecommerce brand. Please email jobs@brandco.com",
]

NOT_BUYERS = [
    "Looking for my next role as an AI video editor. Open to remote.",
    "I'm looking for an AI video editor job, 3 years experience. #opentowork",
    "Looking for 2 D2C brands to try AI UGC ads with us this month.",
    "I'm an AI UGC creator. Looking for collaborations with skincare brands!",
    "We're hiring an AI video creator for our client, a fast-growing DTC brand. Send CVs to jane@talentco.com",
    "Stop hiring video editors. Every brand needs AI UGC now. Here's why.",
    "Why every brand needs AI commercials in 2026. 5 reasons.",
    "I help DTC brands scale with AI UGC ads. DM me 'UGC' to get a sample.",
    "We create AI commercials for brands. Book a call to see our reel.",
    "Our services: AI product ads, AI UGC, AI films. Slots open this month.",
    "You don't need a production crew anymore. AI ads are here.",
    "Recruiting for an AI Creative Producer on behalf of a London agency.",
    "We're an AI video studio. Looking for brands that want AI launch ads.",
    "Here's how we made a Nike-style AI commercial in 2 days.",
    "Available for freelance AI video projects. Check out my portfolio.",
    "Seeking a new role in AI content creation. Let's connect.",
]


def run():
    import li_lib as L
    import li_build as LB
    cfg = L.config()
    today = L.TODAY.isoformat()
    misses = []
    for t in BUYERS:
        why = LB.judge_post({"text": t, "date": today}, 21, cfg)
        if why:
            misses.append(f"BUYER REJECTED ({why}): {t[:70]}")
    for t in NOT_BUYERS:
        if not LB.judge_post({"text": t, "date": today}, 21, cfg):
            misses.append(f"NON-BUYER KEPT: {t[:70]}")
    return misses


if __name__ == "__main__":
    m = run()
    print("\n".join(m) if m else f"all {len(BUYERS) + len(NOT_BUYERS)} labelled posts judged correctly")
