"""Tests for wardrobe service."""
import pytest
from fastapi import HTTPException
from services.wardrobe_service import (
    add_garment, list_garments, get_garment,
    delete_garment, toggle_favorite, get_wardrobe_stats,
)


class TestAddGarment:
    def test_add_garment_default(self, test_user_id):
        garment = add_garment(test_user_id)
        assert garment.id.startswith("g_")
        assert garment.user_id == test_user_id
        assert garment.attributes.category.value == "top"

    def test_add_garment_with_category(self, test_user_id):
        garment = add_garment(test_user_id, category="shoes")
        assert garment.attributes.category.value == "shoes"


class TestListGarments:
    def test_list_empty(self):
        items = list_garments("nonexistent_user")
        assert items == []

    def test_list_all(self, test_user_id):
        add_garment(test_user_id)
        add_garment(test_user_id)
        items = list_garments(test_user_id)
        assert len(items) == 2

    def test_filter_by_category(self, test_user_id):
        add_garment(test_user_id, category="top")
        add_garment(test_user_id, category="shoes")
        tops = list_garments(test_user_id, category="top")
        assert len(tops) == 1
        assert tops[0].attributes.category.value == "top"

    def test_filter_by_favorite(self, test_user_id):
        g = add_garment(test_user_id)
        toggle_favorite(test_user_id, g.id)
        favs = list_garments(test_user_id, is_favorite=True)
        assert len(favs) == 1


class TestGetGarment:
    def test_get_existing(self, test_user_id):
        g = add_garment(test_user_id)
        found = get_garment(test_user_id, g.id)
        assert found.id == g.id

    def test_get_not_found(self, test_user_id):
        with pytest.raises(HTTPException) as exc_info:
            get_garment(test_user_id, "nonexistent")
        assert exc_info.value.status_code == 404


class TestDeleteGarment:
    def test_delete(self, test_user_id):
        g = add_garment(test_user_id)
        result = delete_garment(test_user_id, g.id)
        assert result["status"] == "deleted"
        items = list_garments(test_user_id)
        assert len(items) == 0


class TestToggleFavorite:
    def test_toggle_on(self, test_user_id):
        g = add_garment(test_user_id)
        result = toggle_favorite(test_user_id, g.id)
        assert result["is_favorite"] is True

    def test_toggle_off(self, test_user_id):
        g = add_garment(test_user_id)
        toggle_favorite(test_user_id, g.id)  # on
        result = toggle_favorite(test_user_id, g.id)  # off
        assert result["is_favorite"] is False

    def test_toggle_not_found(self, test_user_id):
        with pytest.raises(HTTPException):
            toggle_favorite(test_user_id, "nonexistent")


class TestWardrobeStats:
    def test_empty_stats(self, test_user_id):
        stats = get_wardrobe_stats(test_user_id)
        assert stats["total_items"] == 0
        assert stats["favorites"] == 0

    def test_stats_with_items(self, test_user_id):
        add_garment(test_user_id, category="top")
        add_garment(test_user_id, category="top")
        add_garment(test_user_id, category="shoes")
        stats = get_wardrobe_stats(test_user_id)
        assert stats["total_items"] == 3
        assert stats["by_category"]["top"] == 2
        assert stats["by_category"]["shoes"] == 1
