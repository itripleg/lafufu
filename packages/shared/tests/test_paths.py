from lafufu_shared import paths


def test_image_letterheads_dir_ends_with_images_letterheads():
    p = paths.image_letterheads_dir()
    assert p.parts[-1] == "letterheads"
    assert p.parts[-2] == "images"


def test_image_letterheads_dir_contains_data():
    p = paths.image_letterheads_dir()
    assert "data" in p.parts


def test_image_sprites_dir_ends_with_images_sprites():
    p = paths.image_sprites_dir()
    assert p.parts[-1] == "sprites"
    assert p.parts[-2] == "images"


def test_image_sprites_dir_contains_data():
    p = paths.image_sprites_dir()
    assert "data" in p.parts


def test_image_letterheads_defaults_dir_ends_with_images_letterheads():
    p = paths.image_letterheads_defaults_dir()
    assert p.parts[-1] == "letterheads"
    assert p.parts[-2] == "images"


def test_image_letterheads_defaults_dir_contains_assets():
    p = paths.image_letterheads_defaults_dir()
    assert "assets" in p.parts


def test_image_sprites_defaults_dir_ends_with_images_sprites():
    p = paths.image_sprites_defaults_dir()
    assert p.parts[-1] == "sprites"
    assert p.parts[-2] == "images"


def test_image_sprites_defaults_dir_contains_assets():
    p = paths.image_sprites_defaults_dir()
    assert "assets" in p.parts
