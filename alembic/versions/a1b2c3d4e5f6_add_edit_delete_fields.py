"""add_edit_delete_fields

Revision ID: a1b2c3d4e5f6
Revises: 66b89de0233b
Create Date: 2026-04-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '66b89de0233b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # chat_messages: nouveaux champs pour edit/delete
    op.add_column('chat_messages', sa.Column('sequence_number', sa.Integer(), nullable=True))
    op.add_column('chat_messages', sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')))
    op.add_column('chat_messages', sa.Column('previous_content', sa.Text(), nullable=True))
    op.add_column('chat_messages', sa.Column('agent_id', sa.String(length=100), nullable=True))
    op.add_column('chat_messages', sa.Column('processing_ms', sa.Integer(), nullable=True))

    # chat_threads: champs pour compression mémoire + compteur
    op.add_column('chat_threads', sa.Column('compressed_summary', sa.Text(), nullable=True))
    op.add_column('chat_threads', sa.Column('summary_up_to', sa.Integer(), nullable=True))
    op.add_column('chat_threads', sa.Column('message_count', sa.Integer(), nullable=False, server_default=sa.text('0')))


def downgrade() -> None:
    op.drop_column('chat_threads', 'message_count')
    op.drop_column('chat_threads', 'summary_up_to')
    op.drop_column('chat_threads', 'compressed_summary')

    op.drop_column('chat_messages', 'processing_ms')
    op.drop_column('chat_messages', 'agent_id')
    op.drop_column('chat_messages', 'previous_content')
    op.drop_column('chat_messages', 'is_active')
    op.drop_column('chat_messages', 'sequence_number')
