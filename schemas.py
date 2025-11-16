"""
Database Schemas

Define your MongoDB collection schemas here using Pydantic models.
These schemas are used for data validation in your application.

Each Pydantic model represents a collection in your database.
Model name is converted to lowercase for the collection name:
- User -> "user" collection
- Product -> "product" collection
- BlogPost -> "blogs" collection
"""

from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List
from datetime import datetime

# --- App Schemas for the Hourly Raffle ---

class Entry(BaseModel):
    """
    Entries for the hourly draw
    Collection name: "entry"
    """
    name: str = Field(..., description="Participant full name", min_length=2)
    email: EmailStr = Field(..., description="Participant email (unique per draw hour)")
    draw_id: str = Field(..., description="Identifier for the draw (YYYYMMDDHH)")
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

class Draw(BaseModel):
    """
    An hourly draw metadata
    Collection name: "draw"
    """
    draw_id: str = Field(..., description="Identifier for the draw (YYYYMMDDHH)")
    starts_at: datetime = Field(..., description="UTC datetime when the hour starts")
    ends_at: datetime = Field(..., description="UTC datetime when the hour ends")
    prize: float = Field(1000.0, description="Prize amount in dollars")
    status: str = Field("open", description="open|closed")
    entries_count: int = Field(0, description="Number of entries")
    winner_entry_id: Optional[str] = Field(None, description="ID of the winning entry")
    winner_name: Optional[str] = None
    winner_email: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

# Example schemas retained for reference (not used by app directly)
class User(BaseModel):
    name: str
    email: str
    address: str
    age: Optional[int] = None
    is_active: bool = True

class Product(BaseModel):
    title: str
    description: Optional[str] = None
    price: float
    category: str
    in_stock: bool = True
