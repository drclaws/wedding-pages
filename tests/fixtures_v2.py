"""Fictional data in the format 2, as Python objects, and helpers around it.

`SITE` / `INVITATIONS` are the main set: eight invitations that cover every
combination of the form of address, a guest from another city and a
companion, a different primary event, hidden and switched-on events, notes
for sections and events, a place that is not announced yet, events on two
days, photos with a portrait and a landscape video and a photo behind the
cover.  `PENDING_SITE` / `PENDING_INVITATIONS` are the set without places and
media (and a cover without a photo).

There are no media files: `MEDIA_INFO` stands for what the build would read
from them.  Use `site()` / `invitations()` for copies that a test may change.
"""

from __future__ import annotations

import copy
from datetime import timedelta

from tools._media import MediaInfo
from tools._page import PageSettings

MEDIA_DIR = "kR1sKvBGXluXnZd73eOXRw"

SITE = {
    "schemaVersion": 2,
    "coupleNames": "Алиса и Боб",
    "rsvpDeadline": "1 мая 2030",
    "mediaDir": MEDIA_DIR,
    "mainEvent": "dinner",
    "texts": {
        "announce": "Мы, {coupleNames}, женимся!",
        "invite": {
            "ty": "Ждём тебя {eventDate} в {eventTime}.\n"
            "Нам очень хочется разделить этот день с тобой.",
            "vy": "Ждём вас {eventDate} в {eventTime}.\n"
            "Нам очень хочется разделить этот день с вами.",
            "all": "Ждём вас {eventDate} в {eventTime}.",
        },
    },
    "sections": [
        {"id": "cover", "type": "cover", "background": "cover", "backgroundFocus": "center"},
        {
            "id": "invite",
            "title": "{greeting}",
            "align": "center",
            "widgets": [
                {
                    "type": "text",
                    "variant": "lead",
                    "text": "{text:announce}",
                },
                {
                    "id": "plus-one",
                    "type": "text",
                    "variant": "lead",
                    "visible": False,
                    "text": {
                        "ty": "Ты можешь прийти со спутником или спутницей.\n"
                        "Пожалуйста, сообщи нам заранее имя своего гостя.",
                        "vy": "Вы можете прийти со спутником или спутницей.\n"
                        "Пожалуйста, сообщите нам заранее имя вашего гостя.",
                    },
                },
                {
                    "type": "text",
                    "variant": "lead",
                    "text": "{text:invite}",
                },
            ],
        },
        {"id": "personal", "title": "Несколько слов лично", "widgets": []},
        {
            "id": "when",
            "title": "Дата и время",
            "align": "center",
            "widgets": [{"type": "date"}],
        },
        {
            "id": "where",
            "title": "Где и когда",
            "width": "wide",
            "widgets": [{"type": "events"}],
        },
        {
            "id": "travel",
            "title": "Гостям из других городов",
            "visible": False,
            "widgets": [
                {
                    "type": "text",
                    "text": "В день праздника от вокзала Энска до усадьбы и обратно будет "
                    "ходить трансфер.\n\nОтправление от вокзала — в 14:45,\nобратно — в 23:15.",
                },
                {"type": "location", "location": "hotel", "variant": "compact"},
                {
                    "id": "hotel-booked",
                    "type": "text",
                    "visible": False,
                    "text": {
                        "ty": "Для тебя забронирован номер в этой гостинице.",
                        "vy": "Для вас забронирован номер в этой гостинице.",
                    },
                },
            ],
        },
        {
            "id": "story",
            "title": "Наша история",
            "widgets": [{"type": "media", "items": ["story-1", "proposal", "story-2"]}],
        },
        {
            "id": "rsvp",
            "title": "Подтверждение",
            "align": "center",
            "widgets": [
                {
                    "type": "text",
                    "text": {
                        "ty": "Пожалуйста, ответь нам до {rsvpDeadline} — там же, где тебе "
                        "пришла эта ссылка.\n\nБудем очень рады встрече!",
                        "vy": "Пожалуйста, ответьте нам до {rsvpDeadline} — там же, где вы "
                        "получили эту ссылку.\n\nБудем очень рады встрече!",
                    },
                },
                {"type": "text", "variant": "signature", "text": "{coupleNames}"},
            ],
        },
    ],
    "events": {
        "ceremony": {
            "title": "Регистрация",
            "location": "registry",
            "start": "2030-06-15T11:00:00+03:00",
            "end": "2030-06-15T12:00:00+03:00",
        },
        "dinner": {
            "title": "Праздничный ужин",
            "location": "manor",
            "start": "2030-06-15T16:00:00+03:00",
            "end": "2030-06-15T23:30:00+03:00",
            "showEnd": True,
            "schedule": "dinner",
        },
        "brunch": {
            "title": "Второй день",
            "location": "terrace",
            "start": "2030-06-16T12:00:00+03:00",
            "end": "2030-06-16T15:00:00+03:00",
            "visible": False,
        },
    },
    "locations": {
        "registry": {
            "name": "Дворец бракосочетания Энска",
            "address": "г. Энск, Центральная площадь, д. 2",
            "geo": {"lat": 12.340001, "lng": 23.450002},
            "maps": {
                "googleQuery": "Гостевой вход, Дворец бракосочетания, Энск",
                "yandexGeoId": "1234567890",
                "appleQuery": "Гостевой вход, Дворец бракосочетания, Энск",
            },
            "photos": ["registry-1"],
        },
        "manor": {
            "name": "Усадьба «Образец»",
            "address": "г. Энск, ул. Примерная, д. 1",
            "description": "Старинная усадьба на берегу пруда в пригороде Энска. Церемония "
            "пройдёт в саду, ужин — в главном зале.\n\nНа территории есть бесплатная "
            "парковка.",
            "geo": {"lat": 12.345678, "lng": 23.456789},
            "maps": {
                "googlePlaceId": "EXAMPLE-not-a-real-place-id",
                "yandexOrgId": "1000000001",
                "applePlaceId": "IEXAMPLE0000001",
            },
            "photos": ["venue-1", "venue-2", "walk", "venue-3", "venue-4"],
            "directions": "directions",
        },
        "terrace": {"ready": False},
        "hotel": {
            "name": "Гостиница «Пример»",
            "address": "г. Энск, Вокзальная ул., д. 5",
            "geo": {"lat": 12.35, "lng": 23.46},
        },
    },
    "schedules": {
        "dinner": [
            {
                "time": "15:30",
                "title": "Сбор гостей",
                "text": "Приветственные напитки и лёгкие закуски в саду.",
            },
            {"time": "16:00", "title": "Церемония", "text": "Выездная регистрация на берегу пруда."},
            {"time": "17:00", "title": "Фотосессия"},
            {
                "time": "18:00",
                "title": "Праздничный ужин",
                "text": "Главный зал усадьбы.\nРассадка — по карточкам.",
            },
            {"time": "23:00", "title": "Завершение вечера"},
        ]
    },
    "media": {
        # the background of the cover: decoration, no description
        "cover": {"type": "image", "file": "cover.png"},
        "registry-1": {"type": "image", "file": "registry-1.png", "alt": "Дворец бракосочетания"},
        "venue-1": {"type": "image", "file": "venue-1.png", "alt": "Усадьба со стороны пруда"},
        "venue-2": {"type": "image", "file": "venue-2.png", "alt": "Сад усадьбы"},
        "venue-3": {"type": "image", "file": "venue-3.png", "alt": "Главный зал"},
        "venue-4": {"type": "image", "file": "venue-4.png", "alt": "Веранда"},
        "directions": {"type": "image", "file": "directions.png", "alt": "Схема проезда"},
        "story-1": {"type": "image", "file": "story-1.png", "alt": "Первая встреча"},
        "story-2": {"type": "image", "file": "story-2.png", "alt": "Помолвка"},
        "proposal": {
            "type": "video",
            "file": "proposal.mp4",
            "poster": "proposal-poster.png",
            "width": 720,
            "height": 1280,
            "alt": "Видео предложения",
        },
        "walk": {
            "type": "video",
            "file": "walk.mp4",
            "poster": "walk-poster.png",
            "width": 1280,
            "height": 720,
            "alt": "Прогулка по усадьбе",
        },
    },
}

INVITATIONS = [
    {
        "token": "0R-pnCqGFgrPOR5e-NluZw",
        "greeting": "Дорогая Кэрол!",
        "form": "ty",
        "events": {"brunch": {"visible": True}},
        "sections": {
            "personal": {"note": "Спасибо, что столько лет рядом. Очень ждём! <3"},
            "invite": {"widgets": {"plus-one": {"visible": True}}},
        },
    },
    {
        "token": "oWaEF01q51e11fwVhCZQqQ",
        "greeting": "Дорогой Дэйв!",
        "form": "ty",
        "events": {
            "dinner": {"note": "Напиши нам, пожалуйста, про аллергию — подберём блюда из меню."}
        },
    },
    {
        "token": "48lh3whME-jqDOw3yzzWpQ",
        "greeting": "Дорогая Ева!",
        "form": "ty",
        "primaryEvent": "ceremony",
        "events": {"brunch": {"visible": True}},
        "sections": {
            "personal": {
                "note": "Помнишь, как мы познакомились в поезде?\nТеперь ты знаешь, чем всё "
                "закончилось.\n\nОбнимаем и ждём!"
            },
            "travel": {
                "visible": True,
                "note": "Встретим тебя на вокзале — просто скажи, каким поездом приедешь.",
            },
        },
    },
    {
        "token": "lXG6kSgXRvYCN6GGk5IcBA",
        "greeting": "Дорогой Трент!",
        "form": "ty",
        "sections": {"invite": {"widgets": {"plus-one": {"visible": True}}}, "travel": {"visible": True}},
    },
    {
        "token": "uGeJJ1NFz9Q2YYhekmwPtw",
        "greeting": "Дорогие Пегги и Виктор!",
        "form": "vy",
        "primaryEvent": "ceremony",
        "sections": {
            "personal": {
                "note": "Вы — пример для нас во всём.\n\nОчень надеемся, что вы разделите с "
                "нами этот день."
            }
        },
    },
    {
        "token": "IBAhKaLEOj1DHuOUdUMzQA",
        "greeting": "Дорогие Грейс и Фрэнк!",
        "form": "vy",
        "sections": {
            "travel": {
                "visible": True,
                "note": "Заезд в гостиницу — накануне, после 14:00.",
                "widgets": {"hotel-booked": {"visible": True}},
            }
        },
    },
    {
        "token": "hoC853fn9btumFjSJTlzpQ",
        "greeting": "Уважаемый Оскар Петрович!",
        "form": "vy",
        "events": {"ceremony": {"visible": False}},
        "sections": {
            "personal": {"note": "Для нас большая честь пригласить вас на наш праздник."},
            "invite": {"widgets": {"plus-one": {"visible": True}}},
        },
    },
    {
        "token": "04e2efb6-33cd-423f-acff-3c14562a3113",
        "greeting": "Уважаемая Хайди Андреевна!",
        "form": "vy",
        "sections": {
            "invite": {"widgets": {"plus-one": {"visible": True}}},
            "travel": {
                "visible": True,
                "note": "Трансфер от вокзала будет ждать вас у главного входа.",
            },
            "where": {
                "note": "Если понадобится помощь с дорогой до усадьбы — напишите нам, пришлём "
                "машину."
            },
        },
    },
]

PENDING_SITE = {
    "schemaVersion": 2,
    "coupleNames": "Алиса и Боб",
    "rsvpDeadline": "1 июля 2030",
    "mediaDir": "MR7IwBP4RVsVlBUFCeyEqQ",
    "mainEvent": "celebration",
    "sections": [
        {"id": "cover", "type": "cover"},
        {
            "id": "invite",
            "title": "{greeting}",
            "align": "center",
            "widgets": [
                {
                    "type": "text",
                    "variant": "lead",
                    "text": {
                        "ty": "Мы, {coupleNames}, приглашаем тебя на наш праздник.",
                        "vy": "Мы, {coupleNames}, приглашаем вас на наш праздник.",
                    },
                },
                {
                    "id": "plus-one",
                    "type": "text",
                    "variant": "lead",
                    "visible": False,
                    "text": {
                        "ty": "Ты можешь прийти со спутником или спутницей.",
                        "vy": "Вы можете прийти со спутником или спутницей.",
                    },
                },
                {
                    "type": "text",
                    "variant": "lead",
                    "text": {
                        "ty": "Ждём тебя {eventDate} в {eventTime}.",
                        "vy": "Ждём вас {eventDate} в {eventTime}.",
                    },
                },
            ],
        },
        {"id": "personal", "title": "Несколько слов лично", "widgets": []},
        {"id": "when", "title": "Дата и время", "align": "center", "widgets": [{"type": "date"}]},
        {"id": "where", "title": "Где и когда", "width": "wide", "widgets": [{"type": "events"}]},
        {
            "id": "travel",
            "title": "Гостям из других городов",
            "visible": False,
            "widgets": [
                {
                    "type": "text",
                    "text": "Подробности о дороге и размещении сообщим вместе с адресом.",
                }
            ],
        },
        {
            "id": "rsvp",
            "title": "Подтверждение",
            "align": "center",
            "widgets": [
                {
                    "type": "text",
                    "text": {
                        "ty": "Пожалуйста, ответь нам до {rsvpDeadline} — там же, где тебе "
                        "пришла эта ссылка.",
                        "vy": "Пожалуйста, ответьте нам до {rsvpDeadline} — там же, где вы "
                        "получили эту ссылку.",
                    },
                },
                {"type": "text", "variant": "signature", "text": "{coupleNames}"},
            ],
        },
    ],
    "events": {
        "celebration": {
            "title": "Праздник",
            "location": "venue",
            "start": "2030-08-10T15:00:00+05:00",
            "end": "2030-08-10T22:00:00+05:00",
            "schedule": "day",
        }
    },
    "locations": {"venue": {"ready": False}},
    "schedules": {
        "day": [
            {"time": "15:00", "title": "Церемония"},
            {"time": "16:30", "title": "Праздничный ужин", "text": "Место уточняется."},
        ]
    },
}

PENDING_INVITATIONS = [
    {
        # a readable token with a random tail (`build.py token --prefix heron`)
        "token": "heron-4tx9",
        "greeting": "Дорогая Сибил!",
        "form": "ty",
        "sections": {
            "travel": {
                "visible": True,
                "note": "Как только станет известно место, пришлём подробности о дороге.",
            }
        },
    },
    {
        # a short readable token: counted in the summary of validate and build
        "token": "quokka",
        "greeting": "Дорогие Уолтер и Венди!",
        "form": "vy",
        "sections": {"personal": {"note": "Очень ждём вас!"}},
    },
    {
        "token": "JhlkiN2-7agdX9Fl6D_Z6Q",
        "greeting": "Уважаемая Джуди Сергеевна!",
        "form": "vy",
        "sections": {"invite": {"widgets": {"plus-one": {"visible": True}}}},
    },
]

#: What the build would read from the media files of `SITE`.
MEDIA_INFO = {
    name: MediaInfo(name, width=1200, height=800)
    for name in (
        "registry-1.png",
        "venue-1.png",
        "venue-2.png",
        "venue-3.png",
        "venue-4.png",
        "story-1.png",
        "story-2.png",
    )
}
MEDIA_INFO.update(
    {
        "directions.png": MediaInfo("directions.png", width=1000, height=1000),
        "cover.png": MediaInfo("cover.png", width=2400, height=1600),
        "proposal-poster.png": MediaInfo("proposal-poster.png", width=720, height=1280),
        "walk-poster.png": MediaInfo("walk-poster.png", width=1280, height=720),
        "proposal.mp4": MediaInfo(
            "proposal.mp4", width=720, height=1280, duration_seconds=20.0,
            faststart=True, video_codec="avc1", hdr=False,
        ),
        "walk.mp4": MediaInfo(
            "walk.mp4", width=1280, height=720, duration_seconds=5.0,
            faststart=True, video_codec="avc1", hdr=False,
        ),
    }
)

SITE_IMAGES = {
    "faviconPath": "/assets/favicon.svg",
    "faviconType": "image/svg+xml",
    "ogImage": "/assets/og.png",
    "ogImageType": "image/png",
    "ogImageWidth": 1200,
    "ogImageHeight": 630,
    # not an image, but a site field of the root all the same
    "themeColor": "#f2efe9",
}
DEFAULT_DURATION = timedelta(hours=6)


def site() -> dict:
    return copy.deepcopy(SITE)


def invitations() -> list:
    return copy.deepcopy(INVITATIONS)


def pending_site() -> dict:
    return copy.deepcopy(PENDING_SITE)


def pending_invitations() -> list:
    return copy.deepcopy(PENDING_INVITATIONS)


def settings(**changes) -> PageSettings:
    """The settings of the fixture pages; keyword arguments replace fields."""
    values = {
        "default_duration": DEFAULT_DURATION,
        "site_images": SITE_IMAGES,
        "media_info": MEDIA_INFO,
    }
    values.update(changes)
    return PageSettings(**values)


class Collector:
    """A report that collects the messages (the interface of the checks)."""

    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, text: str) -> None:
        self.errors.append(text)

    def warning(self, text: str) -> None:
        self.warnings.append(text)

    @property
    def messages(self) -> list[str]:
        return self.errors + self.warnings
