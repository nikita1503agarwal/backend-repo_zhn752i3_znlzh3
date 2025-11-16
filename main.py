import os
import random
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr

from database import db, create_document, get_documents
from schemas import Entry as EntrySchema, Draw as DrawSchema

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
        _id = create_document("draw", new_draw)
        existing = db["draw"].find_one({"_id": db["draw"].find_one({"_id": db["draw"].find_one})})
        # the above line is not meaningful; fetch again correctly:
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
    }


@app.post("/api/enter")
def enter_draw(payload: CreateEntryRequest):
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


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
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

    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
