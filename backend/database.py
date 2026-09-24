"""
Step 1: Database schema for the Meridian OfficeTech backend.

Install:
    pip install sqlalchemy

This just DEFINES the tables - running it directly will create the
database file and tables, but there's no data in it yet. Step 2 (auth)
and Step 3 (endpoints) will build on top of this.

Run:
    python database.py
Produces:
    shopmart.db   (SQLite database file)
"""

import os
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

DATABASE_PATH = os.getenv(
    "DATABASE_PATH", os.path.join(os.path.dirname(__file__), "shopmart.db")
)
os.makedirs(os.path.dirname(DATABASE_PATH) or ".", exist_ok=True)
DATABASE_URL = f"sqlite:///{DATABASE_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, default="Maya Sharma")
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="customer", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    invoices = relationship("Invoice", back_populates="owner")
    chat_sessions = relationship("ChatSession", back_populates="owner")
    leave_requests = relationship("LeaveRequest", back_populates="employee")


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    price = Column(Float, nullable=False)
    # Mirrors the "config bug" from your POC - a product can exist but be
    # deliberately excluded from the invoice dropdown until configured.
    show_in_invoice_dropdown = Column(Integer, default=1)  # 1 = true, 0 = false

    # Customer Storefront enrichment fields
    rating = Column(Float, default=4.5)
    review_count = Column(Integer, default=120)
    category = Column(String, default="Office Tech")
    discount_percent = Column(Integer, default=15)
    image_url = Column(String, nullable=True)
    delivery_estimate = Column(String, default="Tomorrow, 11 AM")


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    client_name = Column(String, nullable=False, default="Unspecified Client")
    quantity = Column(Integer, default=1)
    tax_code = Column(String, default="GST 18%")
    tax_document_filename = Column(String, nullable=True)
    remarks = Column(Text, nullable=True)
    status = Column(String, default="Draft")  # Draft, Submitted, Under Review, Approved
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="invoices")
    product = relationship("Product")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String, default="New conversation")
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="chat_sessions")
    messages = relationship(
        "ChatMessage", back_populates="session", order_by="ChatMessage.created_at"
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=False)
    role = Column(String, nullable=False)  # "user" or "bot"
    kind = Column(String, default="text")  # "text", "matches", "escalate"
    content = Column(Text, nullable=False)  # plain text, or JSON string for "matches"
    created_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("ChatSession", back_populates="messages")


# ------------------ Employee Workspace Models ------------------
class EmployeeMeeting(Base):
    __tablename__ = "employee_meetings"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    date_time = Column(String, nullable=False)  # e.g., "Today, 2:30 PM - 3:15 PM"
    attendees = Column(
        String, nullable=False
    )  # e.g., "CFO, Finance Controller, Audit Team"
    link = Column(String, nullable=False, default="#")
    status = Column(
        String, default="Scheduled"
    )  # "Starting soon", "Scheduled", "Completed"
    tags = Column(String, default="Finance")  # e.g., "Tax, Audit, Budget"


class EmployeeEmail(Base):
    __tablename__ = "employee_emails"

    id = Column(Integer, primary_key=True, index=True)
    sender = Column(String, nullable=False)
    sender_email = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    preview = Column(Text, nullable=False)
    timestamp = Column(String, nullable=False)  # e.g., "10:45 AM"
    is_read = Column(Boolean, default=False)
    is_urgent = Column(Boolean, default=False)


class LeaveRequest(Base):
    __tablename__ = "leave_requests"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    leave_type = Column(
        String, nullable=False
    )  # "Sick Leave", "Casual Leave", "Earned Leave", "Work From Home"
    start_date = Column(String, nullable=False)
    end_date = Column(String, nullable=False)
    days_count = Column(Integer, default=1)
    reason = Column(Text, nullable=False)
    status = Column(String, default="Pending")  # "Pending", "Approved", "Rejected"
    created_at = Column(DateTime, default=datetime.utcnow)

    employee = relationship("User", back_populates="leave_requests")


class CompanyProject(Base):
    __tablename__ = "company_projects"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    client = Column(String, nullable=False)
    status = Column(
        String, default="Active"
    )  # "Active", "In Review", "Planning", "Completed"
    progress_pct = Column(Integer, default=0)
    budget_allocated = Column(Float, default=0.0)
    budget_spent = Column(Float, default=0.0)
    deadline = Column(String, nullable=False)
    finance_lead = Column(String, nullable=False)


class JiraTicket(Base):
    __tablename__ = "jira_tickets"

    id = Column(Integer, primary_key=True, index=True)
    ticket_key = Column(String, unique=True, index=True, nullable=False)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=False)
    reporter_email = Column(String, nullable=False)
    summary = Column(String, nullable=False)
    user_query = Column(Text, nullable=False)
    transcript = Column(Text, nullable=False)
    status = Column(String, default="TO DO")
    priority = Column(String, default="High")
    ticket_url = Column(String, nullable=False)
    is_live = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)

    # Run lightweight schema migrations for existing SQLite database
    with engine.connect() as conn:
        raw_conn = conn.connection
        cursor = raw_conn.cursor()

        # Users table migrations
        cursor.execute("PRAGMA table_info(users)")
        user_cols = [c[1] for c in cursor.fetchall()]
        if "role" not in user_cols:
            cursor.execute("ALTER TABLE users ADD COLUMN role TEXT DEFAULT 'customer'")
        if "name" not in user_cols:
            cursor.execute(
                "ALTER TABLE users ADD COLUMN name TEXT DEFAULT 'Maya Sharma'"
            )

        # Products table migrations
        cursor.execute("PRAGMA table_info(products)")
        prod_cols = [c[1] for c in cursor.fetchall()]
        migrations = [
            ("rating", "REAL DEFAULT 4.5"),
            ("review_count", "INTEGER DEFAULT 120"),
            ("category", "TEXT DEFAULT 'Office Tech'"),
            ("discount_percent", "INTEGER DEFAULT 15"),
            ("image_url", "TEXT"),
            ("delivery_estimate", "TEXT DEFAULT 'Tomorrow, 11 AM'"),
        ]
        for col_name, col_def in migrations:
            if col_name not in prod_cols:
                cursor.execute(f"ALTER TABLE products ADD COLUMN {col_name} {col_def}")
        raw_conn.commit()

    print(
        f"Database initialized at shopmart.db with tables: "
        f"{', '.join(Base.metadata.tables.keys())}"
    )


if __name__ == "__main__":
    init_db()
