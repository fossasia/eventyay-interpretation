import logging

import redis
import requests
from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from eventyay.base.models import Event

from .backends.voxbento_api import subscribe_to_voxbento_webhooks

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=5, retry_backoff=True)
def sync_voxbento_connection(self, event_id: int) -> None:
    """
    Background task to sync the VoxBento OAuth connection by subscribing to webhooks.
    Includes exponential backoff for transient failures.
    """
    try:
        event = Event.objects.get(id=event_id)
        subscribe_to_voxbento_webhooks(event)
    except Event.DoesNotExist:
        logger.warning(f"Event {event_id} does not exist. Cannot sync VoxBento connection.")
        return
    except (requests.RequestException, redis.exceptions.LockError) as e:
        logger.warning(f"Transient error syncing VoxBento connection for event {event_id}: {e}")
        try:
            self.retry(exc=e)
        except MaxRetriesExceededError:
            logger.error(f"Max retries exceeded syncing VoxBento connection for event {event_id}")
            grant = getattr(event, "voxbento_oauth_grant", None)
            if grant:
                grant.webhook_subscription_failed = True
                grant.save(update_fields=["webhook_subscription_failed"])
    except Exception as e:
        # Check if it's a 5xx error from requests.HTTPError
        if isinstance(e, requests.HTTPError) and e.response is not None and e.response.status_code >= 500:
            logger.warning(f"5xx error syncing VoxBento connection for event {event_id}: {e}")
            try:
                self.retry(exc=e)
            except MaxRetriesExceededError:
                logger.error(f"Max retries exceeded syncing VoxBento connection for event {event_id}")
                grant = getattr(event, "voxbento_oauth_grant", None)
                if grant:
                    grant.webhook_subscription_failed = True
                    grant.save(update_fields=["webhook_subscription_failed"])
        else:
            logger.error(f"Unexpected error syncing VoxBento connection for event {event_id}: {e}", exc_info=True)
            grant = getattr(event, "voxbento_oauth_grant", None)
            if grant:
                grant.webhook_subscription_failed = True
                grant.save(update_fields=["webhook_subscription_failed"])
            raise
