"""What every integration must follow (see archiver/integrations/__init__.py)."""
import re
import unittest
from urllib.parse import urlparse

from archiver import i18n, integrations
from archiver.store import source_url
from tests.fixtures import POST_URL, VIDEO_URL


class IntegrationTests(unittest.TestCase):
    def test_every_integration_follows_the_contract(self):
        from archiver.store import MEDIA_ID_PATTERN
        self.assertEqual(len({module.NAME for module in integrations.ALL}), len(integrations.ALL))
        for module in integrations.ALL:
            with self.subTest(module=module.NAME):
                self.assertTrue(callable(module.link) and callable(module.archive))
                self.assertIs(integrations.find(module.NAME), module)
                self.assertIsNone(module.link(urlparse("https://example.com/article")))
                self.assertTrue(callable(module.online))
        for template in (integrations.WRONG_LINK, integrations.DELETED, integrations.UNAVAILABLE, integrations.LIMITED, integrations.UNREADABLE,
                         integrations.NO_MEDIA, integrations.EXPIRED, integrations.NOT_SERVER, integrations.NO_PART):
            message = template.format(platform="Exemple", host="site.example", part=3)
            for lang in i18n.CATALOGS:
                with self.subTest(template=template, lang=lang):
                    translated = i18n.translate(message, lang)
                    self.assertNotEqual(translated, message)
                    self.assertEqual("Exemple" in message, "Exemple" in translated)
        self.assertIsNone(integrations.find("Web"))
        for url, platform in (("https://bsky.app/profile/alice.example/post/3kabc", "Bluesky"),
                              ("https://social.example/@alice/1123", "Mastodon"), (POST_URL, "X"), (VIDEO_URL, "YouTube"),
                              ("https://www.reddit.com/r/example/comments/abc12/title/def34/", "Reddit"),
                              ("https://www.reddit.com/r/example/s/AbCdEf1234", "Reddit")):
            media_id, _, found = source_url(url)
            self.assertEqual(found, platform)
            self.assertTrue(re.fullmatch(MEDIA_ID_PATTERN, media_id + ".2"), media_id)
