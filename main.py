import os
import random
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr

from database import db, create_document, get_documents
from schemas import Entry as EntrySchema, Draw as DrawSchema

# Optional Stripe integration
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY") or os.getenv("STRIPE_API_KEY")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

try:
    import stripe  # type: ignore
    if STRIPE_SECRET_KEY:
        stripe.api_key = STRIPE_SECRET_KEY
except Exception:
    stripe = None

app = FastAPI(title="Hourly Raffle API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Utility functions

def current_draw_id(dt: Optional[datetime] = None) -> str:
    dt = (dt or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return dt.strftime("%Y%m%d%H")


def hour_window(dt: Optional[datetime] = None):
    now = (dt or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start = now.replace(minute=0, second=0, microsecond=0)
    end = start + timedelta(hours=1)
    return start, end


# Request models
class CreateEntryRequest(BaseModel):
    name: str
    email: EmailStr


# Public endpoints
@app.get("/")
def root():
    return {"message": "Hourly raffle backend running"}


@app.get("/api/status")
def api_status():
    """Returns info about the current and previous draw status"""
    draw_id = current_draw_id()
    start, end = hour_window()

    # ensure draw document exists or get latest
    existing = db["draw"].find_one({"draw_id": draw_id}) if db else None
    if not existing and db:
        # create a new draw metadata doc
        new_draw = DrawSchema(
            draw_id=draw_id,
            starts_at=start,
            ends_at=end,
            prize=1000.0,
            status="open",
            entries_count=0,
        )
        _ = create_document("draw", new_draw)
        existing = db["draw"].find_one({"draw_id": draw_id})

    entries_count = 0
    if db:
        entries_count = db["entry"].count_documents({"draw_id": draw_id})
        db["draw"].update_one({"draw_id": draw_id}, {"$set": {"entries_count": entries_count}}, upsert=True)

    # find last closed draw (winner)
    last_winner = None
    if db:
        last_winner = db["draw"].find_one({"status": "closed"}, sort=[("ends_at", -1)])

    return {
        "current": {
            "draw_id": draw_id,
            "starts_at": start.isoformat(),
            "ends_at": end.isoformat(),
            "prize": 1000.0,
            "status": (existing.get("status") if existing else "open"),
            "entries_count": entries_count,
        },
        "last_winner": last_winner,
        "payments": {
            "enabled": bool(STRIPE_SECRET_KEY and stripe),
            "amount": 1000,  # cents
            "currency": "usd",
            "price": 10.00,
        },
    }


@app.post("/api/enter")
def enter_draw(payload: CreateEntryRequest):
    """Legacy free entry endpoint. If payments are enabled, block direct entry."""
    if STRIPE_SECRET_KEY and stripe:
        raise HTTPException(status_code=403, detail="El portal de pagos está activo. Usa el flujo de pago para participar.")

    draw_id = current_draw_id()

    # prevent duplicate email in the same hour
    if db and db["entry"].find_one({"draw_id": draw_id, "email": payload.email}):
        raise HTTPException(status_code=400, detail="Ya estás participando en el sorteo de esta hora.")

    entry = EntrySchema(name=payload.name, email=payload.email, draw_id=draw_id)
    _id = create_document("entry", entry)

    return {"ok": True, "message": "¡Entraste al sorteo de esta hora!", "entry_id": _id, "draw_id": draw_id}


@app.post("/api/close-current")
def close_current_draw():
    """Closes the current hour draw and picks a random winner. This can be triggered by a scheduler or manual call."""
    draw_id = current_draw_id()
    if not db:
        raise HTTPException(status_code=500, detail="Database no disponible")

    start, end = hour_window()

    entries = list(db["entry"].find({"draw_id": draw_id}))
    if len(entries) == 0:
        # close with no winner
        db["draw"].update_one(
            {"draw_id": draw_id},
            {"$set": {
                "draw_id": draw_id,
                "starts_at": start,
                "ends_at": end,
                "status": "closed",
                "prize": 1000.0,
                "entries_count": 0,
                "winner_entry_id": None,
                "winner_name": None,
                "winner_email": None,
                "updated_at": datetime.now(timezone.utc)
            }},
            upsert=True,
        )
        return {"ok": True, "message": "Sorteo cerrado sin participantes."}

    winner = random.choice(entries)

    db["draw"].update_one(
        {"draw_id": draw_id},
        {"$set": {
            "draw_id": draw_id,
            "starts_at": start,
            "ends_at": end,
            "status": "closed",
            "prize": 1000.0,
            "entries_count": len(entries),
            "winner_entry_id": str(winner.get("_id")),
            "winner_name": winner.get("name"),
            "winner_email": winner.get("email"),
            "updated_at": datetime.now(timezone.utc)
        }},
        upsert=True,
    )

    return {"ok": True, "message": "Sorteo cerrado y ganador elegido.", "winner": {
        "name": winner.get("name"),
        "email": winner.get("email"),
    }}


# Payments: Stripe Checkout session creator
@app.post("/api/pay/checkout-session")
def create_checkout_session(payload: CreateEntryRequest):
    if not (stripe and STRIPE_SECRET_KEY):
        raise HTTPException(status_code=503, detail="Pagos no disponibles")

    draw_id = current_draw_id()

    # prevent duplicate before charging
    if db and db["entry"].find_one({"draw_id": draw_id, "email": payload.email}):
        raise HTTPException(status_code=400, detail="Ya estás participando en el sorteo de esta hora.")

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[{
                "quantity": 1,
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": f"Entrada sorteo {draw_id}"},
                    "unit_amount": 1000,  # $10.00 in cents
                },
            }],
            metadata={
                "draw_id": draw_id,
                "name": payload.name,
                "email": payload.email,
            },
            success_url=f"{FRONTEND_URL}?success=true&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{FRONTEND_URL}?canceled=true",
        )
        return {"id": session.id, "url": session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/pay/confirm")
def confirm_checkout(session_id: str = Query(..., description="Stripe Checkout Session ID")):
    if not (stripe and STRIPE_SECRET_KEY):
        raise HTTPException(status_code=503, detail="Pagos no disponibles")

    try:
        session = stripe.checkout.Session.retrieve(session_id)
        if session.get("payment_status") != "paid":
            raise HTTPException(status_code=402, detail="Pago no completado")

        meta = session.get("metadata") or {}
        draw_id = meta.get("draw_id") or current_draw_id()
        name = meta.get("name")
        email = meta.get("email")
        if not (name and email):
            raise HTTPException(status_code=400, detail="Datos de participante incompletos")

        # idempotency: if entry exists, return ok
        if db and db["entry"].find_one({"draw_id": draw_id, "email": email}):
            return {"ok": True, "message": "Pago confirmado. Ya estabas registrado en este sorteo."}

        entry = EntrySchema(name=name, email=email, draw_id=draw_id)
        _id = create_document("entry", entry)
        return {"ok": True, "message": "Pago confirmado y participación registrada.", "entry_id": _id, "draw_id": draw_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": [],
        "payments": "Disabled"
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"

            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"

    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"

    # payments status
    response["payments"] = "✅ Enabled" if (stripe and STRIPE_SECRET_KEY) else "Disabled"

    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
