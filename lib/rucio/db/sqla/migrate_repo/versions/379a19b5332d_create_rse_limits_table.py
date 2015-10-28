# Copyright European Organization for Nuclear Research (CERN)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# You may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
#
# Authors:
# - Wen Guan, <wen.guan@cern.ch>, 2015

"""create rse transfer limits table

Revision ID: 379a19b5332d
Revises: 3c9df354071b
Create Date: 2015-10-24 14:36:09.499710

"""

# revision identifiers, used by Alembic.
revision = '379a19b5332d'
down_revision = '3c9df354071b'

from alembic import context, op
import sqlalchemy as sa

from rucio.db.sqla.types import GUID


def upgrade():
    op.create_table('rse_transfer_limits',
                    sa.Column('rse_id', GUID()),
                    sa.Column('activity', sa.String(50)),
                    sa.Column('rse_expression', sa.String(3000)),
                    sa.Column('max_transfers', sa.BigInteger),
                    sa.Column('transfers', sa.BigInteger),
                    sa.Column('waitings', sa.BigInteger),
                    sa.Column('updated_at', sa.DateTime),
                    sa.Column('created_at', sa.DateTime))

    if context.get_context().dialect.name != 'sqlite':
        op.create_primary_key('RSE_TRANSFER_LIMITS_PK', 'rse_transfer_limits', ['rse_id', 'activity'])
        op.create_check_constraint('RSE_TRANSFER_LIMITS_CREATED_NN', 'rse_transfer_limits', 'created_at is not null')
        op.create_check_constraint('RSE_TRANSFER_LIMITS_UPDATED_NN', 'rse_transfer_limits', 'updated_at is not null')
        op.create_foreign_key('RSE_TRANSFER_LIMITS_RSE_ID_FK', 'rse_transfer_limits', 'rses', ['rse_id'], ['id'])


def downgrade():
    if context.get_context().dialect.name == 'postgresql':
        op.drop_constraint('RSE_TRANSFER_LIMITS_PK', 'rse_transfer_limits', type_='primary')
        op.drop_constraint('RSE_TRANSFER_LIMITS_CREATED_NN', 'rse_transfer_limits')
        op.drop_constraint('RSE_TRANSFER_LIMITS_UPDATED_NN', 'rse_transfer_limits')
        op.drop_constraint('RSE_TRANSFER_LIMITS_RSE_ID_FK', 'rse_transfer_limits')
    op.drop_table('rse_transfer_limits')
