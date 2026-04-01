from .database import Base, engine
from . import models  # noqa: F401  # Ensure model metadata is registered.


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    init_db()
    print("Database tables created.")

