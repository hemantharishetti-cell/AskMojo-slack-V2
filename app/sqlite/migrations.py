"""
Database migration utilities to add missing columns to existing tables.
"""
from sqlalchemy import text, inspect
from app.sqlite.database import engine


def migrate_documents_table():
    """
    Add missing columns to the documents table if they don't exist.
    """
    inspector = inspect(engine)
    
    # Check if documents table exists
    if "documents" not in inspector.get_table_names():
        return  # Table doesn't exist, will be created by create_all()
    
    # Get existing columns
    existing_columns = [col["name"] for col in inspector.get_columns("documents")]
    
    with engine.connect() as conn:
        # Add source_type if missing (with default value for existing rows)
        if "source_type" not in existing_columns:
            print("Adding 'source_type' column to documents table...")
            conn.execute(text("ALTER TABLE documents ADD COLUMN source_type VARCHAR NOT NULL DEFAULT 'pdf'"))
            conn.commit()
            print("✓ Added 'source_type' column")
        
        # Add file_name if missing
        if "file_name" not in existing_columns:
            print("Adding 'file_name' column to documents table...")
            conn.execute(text("ALTER TABLE documents ADD COLUMN file_name VARCHAR"))
            conn.commit()
            print("✓ Added 'file_name' column")
        
        # Add file_path if missing
        if "file_path" not in existing_columns:
            print("Adding 'file_path' column to documents table...")
            conn.execute(text("ALTER TABLE documents ADD COLUMN file_path VARCHAR"))
            conn.commit()
            print("✓ Added 'file_path' column")
        
        # Add processed if missing (SQLite uses INTEGER for booleans: 0=False, 1=True)
        if "processed" not in existing_columns:
            print("Adding 'processed' column to documents table...")
            conn.execute(text("ALTER TABLE documents ADD COLUMN processed INTEGER NOT NULL DEFAULT 0"))
            conn.commit()
            print("✓ Added 'processed' column")


def migrate_document_chunks_table():
    """
    Add missing columns to the document_chunks table if they don't exist.
    """
    inspector = inspect(engine)
    
    # Check if document_chunks table exists
    if "document_chunks" not in inspector.get_table_names():
        return  # Table doesn't exist, will be created by create_all()
    
    # Get existing columns
    existing_columns = [col["name"] for col in inspector.get_columns("document_chunks")]
    
    with engine.connect() as conn:
        # Add version if missing (version number, denormalized for quick access)
        if "version" not in existing_columns:
            print("Adding 'version' column to document_chunks table...")
            conn.execute(text("ALTER TABLE document_chunks ADD COLUMN version INTEGER NOT NULL DEFAULT 1"))
            conn.commit()
            print("✓ Added 'version' column to document_chunks")


def migrate_users_table():
    """
    Add missing columns to the users table if they don't exist.
    """
    inspector = inspect(engine)
    
    # Check if users table exists
    if "users" not in inspector.get_table_names():
        return  # Table doesn't exist, will be created by create_all()
    
    # Get existing columns
    existing_columns = [col["name"] for col in inspector.get_columns("users")]
    
    with engine.connect() as conn:
        # Add password if missing (required for authentication)
        if "password" not in existing_columns:
            print("Adding 'password' column to users table...")
            # For existing users, set a default password that needs to be changed
            # In production, you should force password reset
            # SQLite requires DEFAULT for NOT NULL columns when adding to existing table
            try:
                conn.execute(text("ALTER TABLE users ADD COLUMN password VARCHAR NOT NULL DEFAULT 'changeme'"))
                conn.commit()
                print("✓ Added 'password' column (existing users need to set password)")
            except Exception as e:
                # If that fails, try without NOT NULL first, then update
                print(f"  Attempting alternative migration approach...")
                conn.rollback()
                conn.execute(text("ALTER TABLE users ADD COLUMN password VARCHAR"))
                conn.commit()
                # Update existing rows with default password
                conn.execute(text("UPDATE users SET password = 'changeme' WHERE password IS NULL"))
                conn.commit()
                print("✓ Added 'password' column (existing users need to set password)")
        
        # Add is_active if missing (SQLite uses INTEGER for booleans: 0=False, 1=True)
        if "is_active" not in existing_columns:
            print("Adding 'is_active' column to users table...")
            conn.execute(text("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"))
            conn.commit()
            print("✓ Added 'is_active' column")
        
        # Update role default if needed (ensure existing users have a role)
        # Note: SQLite doesn't support ALTER COLUMN, so we'll handle this in application logic


def migrate_slack_integrations_table():
    """
    Create slack_integrations table if it doesn't exist.
    This table is created by Base.metadata.create_all(), but we include it here for completeness.
    """
    inspector = inspect(engine)
    
    # Check if slack_integrations table exists
    if "slack_integrations" not in inspector.get_table_names():
        return  # Table doesn't exist, will be created by create_all()
    
    # Table should already exist if models are imported
    print("✓ slack_integrations table exists")


def migrate_slack_users_table():
    """
    Create slack_users table if it doesn't exist.
    This table is created by Base.metadata.create_all(), but we include it here for completeness.
    """
    inspector = inspect(engine)
    
    # Check if slack_users table exists
    if "slack_users" not in inspector.get_table_names():
        return  # Table doesn't exist, will be created by create_all()
    
    # Table should already exist if models are imported
    print("✓ slack_users table exists")


def migrate_categories_table():
    """
    Create categories table if it doesn't exist.
    This table is created by Base.metadata.create_all(), but we include it here for completeness.
    """
    inspector = inspect(engine)
    
    # Check if categories table exists
    if "categories" not in inspector.get_table_names():
        return  # Table doesn't exist, will be created by create_all()
    
    # Table should already exist if models are imported
    print("✓ categories table exists")


def migrate_documents_category_id():
    """
    Add category_id column to documents table if it doesn't exist.
    This allows documents to reference the categories table via foreign key.
    """
    inspector = inspect(engine)
    
    # Check if documents table exists
    if "documents" not in inspector.get_table_names():
        return  # Table doesn't exist, will be created by create_all()
    
    # Get existing columns
    existing_columns = [col["name"] for col in inspector.get_columns("documents")]
    
    with engine.connect() as conn:
        # Add category_id if missing (nullable foreign key to categories table)
        if "category_id" not in existing_columns:
            print("Adding 'category_id' column to documents table...")
            conn.execute(text("ALTER TABLE documents ADD COLUMN category_id INTEGER"))
            # Note: SQLite doesn't support adding foreign key constraints via ALTER TABLE
            # The foreign key relationship is enforced by SQLAlchemy ORM
            conn.commit()
            print("✓ Added 'category_id' column to documents table")


def migrate_document_upload_logs_table():
    """
    Add time and token usage fields to document_upload_logs table if they don't exist.
    """
    inspector = inspect(engine)
    if "document_upload_logs" not in inspector.get_table_names():
        print("Creating 'document_upload_logs' table...")
        # Table will be created by Base.metadata.create_all()
        return
    
    print("✓ 'document_upload_logs' table exists")
    
    # Add new fields if they don't exist
    existing_columns = [col["name"] for col in inspector.get_columns("document_upload_logs")]
    
    with engine.connect() as conn:
        # Add time tracking fields
        if "upload_time_seconds" not in existing_columns:
            print("Adding time tracking columns to document_upload_logs table...")
            conn.execute(text("ALTER TABLE document_upload_logs ADD COLUMN upload_time_seconds REAL"))
            conn.execute(text("ALTER TABLE document_upload_logs ADD COLUMN description_generation_time_seconds REAL"))
            conn.commit()
            print("✓ Added time tracking columns")
        
        # Add token usage fields
        if "description_tokens_used" not in existing_columns:
            print("Adding token usage columns to document_upload_logs table...")
            conn.execute(text("ALTER TABLE document_upload_logs ADD COLUMN description_tokens_used INTEGER"))
            conn.execute(text("ALTER TABLE document_upload_logs ADD COLUMN description_tokens_prompt INTEGER"))
            conn.execute(text("ALTER TABLE document_upload_logs ADD COLUMN description_tokens_completion INTEGER"))
            conn.commit()
            print("✓ Added token usage columns")


def migrate_slack_integrations_socket_mode():
    """
    Add Socket Mode fields to slack_integrations table if they don't exist.
    """
    inspector = inspect(engine)
    if "slack_integrations" not in inspector.get_table_names():
        print("Creating 'slack_integrations' table...")
        # Table will be created by Base.metadata.create_all()
        return
    
    print("✓ 'slack_integrations' table exists")
    
    # Add new fields if they don't exist
    existing_columns = [col["name"] for col in inspector.get_columns("slack_integrations")]
    
    with engine.connect() as conn:
        # Add Socket Mode fields
        if "app_token" not in existing_columns:
            print("Adding Socket Mode columns to slack_integrations table...")
            conn.execute(text("ALTER TABLE slack_integrations ADD COLUMN app_token TEXT"))
            conn.execute(text("ALTER TABLE slack_integrations ADD COLUMN socket_mode_enabled BOOLEAN DEFAULT 0"))
            conn.commit()
            print("✓ Added Socket Mode columns")


def migrate_query_logs_table():
    """
    Add comprehensive logging fields to query_logs table if they don't exist.
    """
    inspector = inspect(engine)
    if "query_logs" not in inspector.get_table_names():
        print("Creating 'query_logs' table...")
        # Table will be created by Base.metadata.create_all()
        return
    
    print("✓ 'query_logs' table exists")
    
    # Add new comprehensive logging fields if they don't exist
    existing_columns = [col["name"] for col in inspector.get_columns("query_logs")]
    
    with engine.connect() as conn:
        # Add answer field
        if "answer" not in existing_columns:
            print("Adding 'answer' column to query_logs table...")
            conn.execute(text("ALTER TABLE query_logs ADD COLUMN answer TEXT"))
            conn.commit()
            print("✓ Added 'answer' column")
        
        # Add processing_time_seconds field
        if "processing_time_seconds" not in existing_columns:
            print("Adding 'processing_time_seconds' column to query_logs table...")
            conn.execute(text("ALTER TABLE query_logs ADD COLUMN processing_time_seconds REAL"))
            conn.commit()
            print("✓ Added 'processing_time_seconds' column")
        
        # Add token usage fields
        if "total_tokens_used" not in existing_columns:
            print("Adding token usage columns to query_logs table...")
            conn.execute(text("ALTER TABLE query_logs ADD COLUMN total_tokens_used INTEGER"))
            conn.execute(text("ALTER TABLE query_logs ADD COLUMN total_tokens_without_toon INTEGER"))
            conn.execute(text("ALTER TABLE query_logs ADD COLUMN token_savings INTEGER"))
            conn.execute(text("ALTER TABLE query_logs ADD COLUMN token_savings_percent REAL"))
            conn.commit()
            print("✓ Added token usage columns")
        
        # Add JSON fields
        if "token_usage_json" not in existing_columns:
            print("Adding JSON columns to query_logs table...")
            conn.execute(text("ALTER TABLE query_logs ADD COLUMN token_usage_json TEXT"))
            conn.execute(text("ALTER TABLE query_logs ADD COLUMN api_calls_json TEXT"))
            conn.execute(text("ALTER TABLE query_logs ADD COLUMN toon_savings_json TEXT"))
            conn.commit()
            print("✓ Added JSON columns")
        
        # Add slack_user_email field
        if "slack_user_email" not in existing_columns:
            print("Adding 'slack_user_email' column to query_logs table...")
            conn.execute(text("ALTER TABLE query_logs ADD COLUMN slack_user_email TEXT"))
            conn.commit()
            print("✓ Added 'slack_user_email' column")


def run_migrations():
    """
    Run all migrations.
    """
    print("Running database migrations...")
    migrate_users_table()
    migrate_documents_table()
    migrate_document_chunks_table()
    migrate_slack_integrations_table()
    migrate_slack_users_table()
    migrate_categories_table()
    migrate_documents_category_id()
    migrate_document_upload_logs_table()
    migrate_slack_integrations_socket_mode()
    migrate_query_logs_table()
    print("Migrations complete!")

