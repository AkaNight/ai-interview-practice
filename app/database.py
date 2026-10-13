from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import sessionmaker

from app.models import Base


def create_database(data_dir):
    data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{(data_dir / 'interviews.sqlite3').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")

    Base.metadata.create_all(engine)
    # Add nullable Phase 2 fields without resetting existing Phase 1 records.
    fields = {"audio_path": "TEXT", "audio_mime": "TEXT", "answer_transcript_raw": "TEXT",
              "answer_duration": "FLOAT", "transcription_provider": "TEXT", "transcription_model": "TEXT", "recording_id": "TEXT",
              "feedback_json": "JSON", "group_json": "JSON"}
    columns = {column["name"] for column in inspect(engine).get_columns("interview_turns")}
    with engine.begin() as connection:
        for name, sql_type in fields.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE interview_turns ADD COLUMN {name} {sql_type}"))
    return engine, sessionmaker(bind=engine, expire_on_commit=False)
