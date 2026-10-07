from sqlalchemy import select

from studgroup.models import MaterialLink


async def links(db, group_id, keys):
    rows = (
        await db.scalars(
            select(MaterialLink)
            .where(MaterialLink.group_id == group_id, MaterialLink.entity_key.in_(keys))
            .order_by(MaterialLink.title, MaterialLink.id)
        )
    ).all()
    return [{"id": r.id, "title": r.title, "url": r.url} for r in rows]
