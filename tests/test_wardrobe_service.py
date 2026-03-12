"""Tests for wardrobe service."""
import pytest
from fastapi import HTTPException
from services.wardrobe_service import (
    add_garment, list_garments, get_garment,
    delete_garment, toggle_favorite, get_wardrobe_stats,
)


class TestAddGarment:
    def test_add_garment_default(self):
        garment = add_garment("user1")
        assert garment.id.startswith("g_")
        assert garment.user_id == "user1"
        assert garment.attributes.category.value == "top"

    def test_add_garment_with_category(self):
        garment = add_garment("user1", category="shoes")
        assert garment.attributes.category.value == "shoes"


class TestListGarments:
    def test_list_empty(self):
        items = list_garments("empty_user")
        assert items == []

    def test_list_all(self):
        add_garment("user1")
        add_garment("user1")
        items = list_garments("user1")
        assert len(items) == 2

    def test_filter_by_category(self):
        add_garment("user1", category="top")
        add_garment("user1", category="shoes")
        tops = list_garments("user1", category="top")
        assert len(tops) == 1
        assert tops[0].attributes.category.value == "top"

    def test_filter_by_favorite(self):
        g = add_garment("user1")
        toggle_favorite("user1", g.id)
        favs = list_garments("user1", is_favorite=True)
        assert len(favs) == 1


class TestGetGarment:
    def test_get_existing(self):
        g = add_garment("user1")
        found = get_garment("user1", g.id)
        assert found.id == g.id

    def test_get_not_found(self):
        with pytest.raises(HTTPException) as exc_info:
            get_garment("user1", "nonexistent")
        assert exc_info.value.status_code == 404


class TestDeleteGarment:
    def test_delete(self):
        g = add_garment("user1")
        result = delete_garment("user1", g.id)
        assert result["status"] == "deleted"
        items = list_garments("user1")
        assert len(items) == 0


class TestToggleFavorite:
    def test_toggle_on(self):
        g = add_garment("user1")
        result = toggle_favorite("user1", g.id)
        assert result["is_favorite"] is True

    def test_toggle_off(self):
        g = add_garment("user1")
        toggle_favorite("user1", g.id)  # on
        result = toggle_favorite("user1", g.id)  # off
        assert result["is_favorite"] is False

    def test_toggle_not_found(self):
        with pytest.raises(HTTPException):
            toggle_favorite("user1", "nonexistent")


class TestWardrobeStats:
    def test_empty_stats(self):
        stats = get_wardrobe_stats("user1")
        assert stats["total_items"] == 0
        assert stats["favorites"] == 0

    def test_stats_with_items(self):
        add_garment("user1", category="top")
        add_garment("user1", category="top")
        add_garment("user1", category="shoes")
        stats = get_wardrobe_stats("user1")
        assert stats["total_items"] == 3
        assert stats["by_category"]["top"] == 2
        assert stats["by_category"]["shoes"] == 1
