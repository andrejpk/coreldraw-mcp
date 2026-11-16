"""CorelDRAW shape types and traversal shared by the tool modules."""

CDR_CURVE_SHAPE = 3
CDR_POLYGON_SHAPE = 4
CDR_BITMAP_SHAPE = 5
CDR_TEXT_SHAPE = 6
CDR_GROUP_SHAPE = 7


def walk_shapes(shapes, parent_id=None):
    for shape in shapes:
        yield shape, parent_id
        if shape.Type == CDR_GROUP_SHAPE:
            yield from walk_shapes(shape.Shapes, str(shape.StaticID))
