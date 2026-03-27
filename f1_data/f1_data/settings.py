from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

SECRET_KEY = 'django-insecure-g19x0v)+0o9^ik=25hr_rrv$9^40_&^tcez8w*!@k(@8f+zw5w'

DEBUG = True

ALLOWED_HOSTS = []

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core',
    'predictions',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'f1_data.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'f1_data.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# FastF1
FASTF1_CACHE_DIR = os.path.join(BASE_DIR, 'fastf1_cache')

# Fantasy lineup budget in $M. F1 Fantasy increases this over time as driver
# prices inflate across seasons. Update here when the game changes it.
LINEUP_BUDGET: float = 100.0

# Minimum predicted point gain required for the ILP optimizer to make a transfer.
# Set to current MAE so only statistically meaningful improvements trigger changes.
# Tune this down as prediction accuracy improves.
ILP_TRANSFER_THRESHOLD: float = 10

# ML pipeline versions to use for next_race and score_lineup.
# Update these when a new version outperforms the current one in backtesting.
#   ML_FEATURE_STORE: "v1" | "v2" | "v3"
#   ML_PREDICTOR:     "v1" | "v2"
#   ML_OPTIMIZER:     "v1" | "v2" | "v3"
PRICE_SENSITIVITY: float = 1.0  # tune via: backtest --price-sensitivity 0 1 2 3 5 8 10 15 20

ML_FEATURE_STORE: str = "v2"
ML_PREDICTOR: str = "v4"
ML_OPTIMIZER: str = "v3"

# Valid versions for each component (update when adding new versions).
# Management commands import from here so choices stay in one place.
ML_FEATURE_STORE_VERSIONS: list[str] = ["v1", "v2", "v3"]

# Default finishing/qualifying position used for drivers and teams that have
# NO cross-season history at all (true rookies, brand-new constructors).
# Set to ~80th percentile of a 22-driver grid (bottom quintile), reflecting
# the realistic expectation that new entrants start near the back.
# Update this value when the grid size changes significantly.
NEW_ENTRANT_POSITION_DEFAULT: float = 18.0
ML_PREDICTOR_VERSIONS: list[str] = ["v1", "v2", "v3", "v4"]
ML_PREDICTOR_V3_HALF_LIFE: int = 10  # events; tune by updating and re-running backtest
ML_OPTIMIZER_VERSIONS: list[str] = ["v1", "v2", "v3", "v4"]

# Monte Carlo optimizer: number of scenarios to sample per race.
# Higher = more robust candidate diversity but slower. 500 ≈ 0.25s/race with Greedy inner.
MC_N_SCENARIOS: int = 500

# Slack
SLACK_WEBHOOK_URL = os.environ.get('SLACK_WEBHOOK_URL', '')
