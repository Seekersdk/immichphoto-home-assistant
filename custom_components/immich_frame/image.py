"""Image device for Immich Frame integration."""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import random

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import CONF_WATCHED_ALBUMS
from .hub import ImmichHub

SCAN_INTERVAL = timedelta(minutes=5)
_ID_LIST_REFRESH_INTERVAL = timedelta(hours=12)
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Immich Frame image platform."""

    hub = ImmichHub(
        host=config_entry.data[CONF_HOST], api_key=config_entry.data[CONF_API_KEY]
    )

    async_add_entities([ImmichImageFavorite(hass, hub)])

    watched_albums = config_entry.options.get(CONF_WATCHED_ALBUMS, [])
    async_add_entities(
        [
            ImmichImageAlbum(
                hass, hub, album_id=album["id"], album_name=album["albumName"]
            )
            for album in await hub.list_all_albums()
            if album["id"] in watched_albums
        ]
    )

    config_entry.async_on_unload(config_entry.add_update_listener(update_listener))


async def update_listener(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    """Handle options updates."""
    await hass.config_entries.async_reload(config_entry.entry_id)


class BaseImmichImage(ImageEntity):
    """Base image entity for Immich Frame."""

    _attr_has_entity_name = True
    _attr_should_poll = True

    _current_image_bytes: bytes | None = None
    _cached_available_asset_ids: list[str] | None = None
    _available_asset_ids_last_updated = None

    def __init__(self, hass: HomeAssistant, hub: ImmichHub) -> None:
        super().__init__(hass=hass, verify_ssl=True)
        self.hub = hub
        self.hass = hass
        self._attr_extra_state_attributes = {}

    async def async_update(self) -> None:
        await self._load_and_cache_next_image()

    async def async_image(self) -> bytes | None:
        if not self._current_image_bytes:
            await self._load_and_cache_next_image()
        return self._current_image_bytes

    async def _refresh_available_asset_ids(self) -> list[str] | None:
        raise NotImplementedError

    async def _get_next_asset_id(self) -> str | None:
        now = dt_util.utcnow()
        if (
            not self._available_asset_ids_last_updated
            or (now - self._available_asset_ids_last_updated) > _ID_LIST_REFRESH_INTERVAL
        ):
            _LOGGER.debug("Refreshing available asset IDs")
            self._cached_available_asset_ids = await self._refresh_available_asset_ids()
            self._available_asset_ids_last_updated = now

        if not self._cached_available_asset_ids:
            _LOGGER.error("No assets are available")
            return None

        return random.choice(self._cached_available_asset_ids)

    async def _load_and_cache_next_image(self) -> None:
        asset_bytes = None

        while not asset_bytes:
            asset_id = await self._get_next_asset_id()

            if not asset_id:
                return

            asset_bytes = await self.hub.download_asset(asset_id)

            if not asset_bytes:
                await asyncio.sleep(1)
                continue

            asset_info = await self.hub.get_asset_info(asset_id)

            self._attr_extra_state_attributes["media_filename"] = (
                asset_info.get("originalFileName") or ""
            )
            self._attr_extra_state_attributes["media_exif"] = (
                asset_info.get("exifInfo") or ""
            )
            self._attr_extra_state_attributes["media_localdatetime"] = (
                asset_info.get("localDateTime") or ""
            )

            self._current_image_bytes = asset_bytes
            self._attr_image_last_updated = dt_util.utcnow()
            self.async_write_ha_state()


class ImmichImageFavorite(BaseImmichImage):
    """Random favorite image."""

    _attr_unique_id = "immich_frame_favorite_image"
    _attr_name = "Immich Frame: Random favorite image"

    async def _refresh_available_asset_ids(self) -> list[str] | None:
        return [image["id"] for image in await self.hub.list_favorite_images()]


class ImmichImageAlbum(BaseImmichImage):
    """Random image from a specific album."""

    def __init__(
        self, hass: HomeAssistant, hub: ImmichHub, album_id: str, album_name: str
    ) -> None:
        super().__init__(hass, hub)
        self._album_id = album_id
        self._attr_unique_id = f"immich_frame_{album_id}"
        self._attr_name = f"Immich Frame: {album_name}"

    async def _refresh_available_asset_ids(self) -> list[str] | None:
        return [
            image["id"] for image in await self.hub.list_album_images(self._album_id)
        ]
