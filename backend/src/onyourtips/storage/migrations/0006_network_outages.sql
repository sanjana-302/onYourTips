-- 0006: network outages don't count as failures (ADR 0005, "Network outages").
--
-- When the Mac itself is offline (Wi-Fi down, DNS failing), every source and every page fails at
-- once. Those failures say nothing about the sites, so they must not use up a page's 2 attempts
-- or count toward a source's "failed 2 runs in a row" alert.

-- 1 = this extraction attempt counts toward the give-up limit; 0 = failed during an outage.
ALTER TABLE article_content ADD COLUMN counts_as_attempt INTEGER NOT NULL DEFAULT 1;

-- 1 = this source run counts toward failure streaks; 0 = failed during an outage.
ALTER TABLE ingestion_runs ADD COLUMN counts INTEGER NOT NULL DEFAULT 1;

-- One-time repair: extraction attempts that failed because DNS was down.
UPDATE article_content SET counts_as_attempt = 0
 WHERE retryable = 1
   AND (lower(error) LIKE '%nodename nor servname%'
        OR lower(error) LIKE '%name or service not known%'
        OR lower(error) LIKE '%temporary failure in name resolution%'
        OR lower(error) LIKE '%network is unreachable%');

-- One-time repair: source failures in runs where at least half the sources failed on DNS.
UPDATE ingestion_runs SET counts = 0
 WHERE ok = 0
   AND run_id IN (
       SELECT run_id FROM ingestion_runs
        GROUP BY run_id
       HAVING 2 * SUM(ok = 0 AND (lower(error) LIKE '%nodename nor servname%'
                                  OR lower(error) LIKE '%name or service not known%'
                                  OR lower(error) LIKE '%temporary failure in name resolution%'
                                  OR lower(error) LIKE '%network is unreachable%'))
              >= COUNT(*));
