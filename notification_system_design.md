# Stage 1

## REST API Design — Campus Notification Platform

The notification platform supports three types of notifications: Placements, Events, and Results. Students receive these when logged in. The following endpoints form the complete contract.

---

### Authentication

All endpoints require a Bearer token in the `Authorization` header.

```
Authorization: Bearer <access_token>
```

---

### Endpoints

#### Get All Notifications for a Student

```
GET /notifications
```

**Headers**
```json
{
  "Authorization": "Bearer <token>"
}
```

**Response 200**
```json
{
  "notifications": [
    {
      "id": "uuid",
      "studentId": "uuid",
      "type": "Placement | Event | Result",
      "title": "string",
      "message": "string",
      "isRead": false,
      "createdAt": "2026-04-22T17:51:30Z"
    }
  ],
  "total": 42,
  "unreadCount": 10
}
```

---

#### Get Unread Notifications Only

```
GET /notifications?isRead=false
```

**Response 200** — same shape as above, filtered to unread only.

---

#### Mark a Single Notification as Read

```
PATCH /notifications/:id/read
```

**Headers**
```json
{
  "Authorization": "Bearer <token>"
}
```

**Response 200**
```json
{
  "id": "uuid",
  "isRead": true,
  "updatedAt": "2026-04-22T18:00:00Z"
}
```

---

#### Mark All Notifications as Read

```
PATCH /notifications/read-all
```

**Response 200**
```json
{
  "updated": 10,
  "message": "All notifications marked as read"
}
```

---

#### Get Priority Inbox (Top N)

```
GET /notifications/priority?n=10
```

**Response 200**
```json
{
  "top_n": 10,
  "returned": 10,
  "priority_order": "Placement > Result > Event, then by recency",
  "notifications": [
    {
      "id": "uuid",
      "type": "Placement",
      "message": "CSX Corporation hiring",
      "timestamp": "2026-04-22 17:51:18",
      "priorityScore": 3
    }
  ]
}
```

---

#### Delete a Notification

```
DELETE /notifications/:id
```

**Response 200**
```json
{
  "message": "Notification deleted",
  "id": "uuid"
}
```

---

### Real-Time Mechanism

For real-time delivery, the platform uses **WebSockets** (via Socket.IO or native WebSocket). When a new notification is created, the server emits it directly to the connected student's socket room.

```
Event: "new_notification"
Payload:
{
  "id": "uuid",
  "type": "Placement",
  "message": "Google hiring drive announced",
  "createdAt": "2026-04-22T18:00:00Z"
}
```

Students subscribe to their personal room on login using their `studentId`. This avoids polling and delivers sub-second notifications.

---

# Stage 2

## Database Design

### Choice: PostgreSQL

PostgreSQL is the right pick here. Notifications have a well-defined, structured schema (type, studentId, isRead, timestamp) which maps cleanly to relational tables. It supports indexing, JSONB if needed for metadata, and scales well with proper tuning. NoSQL like MongoDB would add flexibility we don't need and make query consistency harder to guarantee.

---

### Schema

```sql
CREATE TYPE notification_type AS ENUM ('Placement', 'Event', 'Result');

CREATE TABLE students (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id UUID NOT NULL REFERENCES students(id) ON DELETE CASCADE,
    type notification_type NOT NULL,
    title VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    is_read BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);
```

---

### Problems as Data Volume Grows

1. **Full table scans** — without indexes, every query for a student's notifications scans all rows.
2. **Replication lag** — at 5M+ rows, replica reads may lag behind writes, causing stale data on reads.
3. **Index bloat** — frequent updates to `is_read` cause write amplification on indexes.
4. **Row-level locking contention** — marking all notifications as read for a student in a loop causes lock waits.

**Solutions:**
- Composite indexes on `(student_id, is_read, created_at DESC)` to cover the most common query pattern.
- Partition the `notifications` table by `created_at` (monthly partitions) to keep active partitions small.
- Archive old notifications (> 90 days) to a cold storage table.
- Use `UPDATE ... WHERE student_id = ? AND is_read = false` as a single bulk statement instead of row-by-row.

---

### SQL Queries

**Fetch unread notifications for a student:**
```sql
SELECT id, type, title, message, created_at
FROM notifications
WHERE student_id = $1 AND is_read = false
ORDER BY created_at DESC;
```

**Mark all as read:**
```sql
UPDATE notifications
SET is_read = true, updated_at = NOW()
WHERE student_id = $1 AND is_read = false;
```

**Count unread:**
```sql
SELECT COUNT(*) FROM notifications
WHERE student_id = $1 AND is_read = false;
```

**Top 10 priority notifications (Placement > Result > Event, then recency):**
```sql
SELECT id, type, message, created_at,
    CASE type
        WHEN 'Placement' THEN 3
        WHEN 'Result' THEN 2
        WHEN 'Event' THEN 1
    END AS priority_score
FROM notifications
WHERE student_id = $1 AND is_read = false
ORDER BY priority_score DESC, created_at DESC
LIMIT 10;
```

---

# Stage 3

## Query Analysis and Optimization

### The Query in Question

```sql
SELECT * FROM notifications
WHERE studentID = 1042 AND isRead = false
ORDER BY createdAt DESC;
```

### Is the Query Accurate?

The query is logically correct — it fetches all unread notifications for a student ordered newest-first. However, `SELECT *` is wasteful. The frontend only needs `id, type, title, message, createdAt`, so selecting every column pulls unnecessary data over the wire and adds I/O cost.

### Why Is It Slow?

At 50,000 students and 5,000,000 notifications, this query hits a table with millions of rows. Without an index on `(studentID, isRead)`, PostgreSQL does a **sequential scan** across all 5M rows to find the matching ones. The `ORDER BY createdAt DESC` then requires a sort, which is expensive if the result set is large.

The estimated cost: a seq scan on 5M rows is O(n). With 50,000 students each hitting this on page load, the DB gets hammered.

### What to Change

Replace `SELECT *` with explicit columns and add a **composite index**:

```sql
CREATE INDEX idx_notifications_student_unread
ON notifications (studentID, isRead, createdAt DESC);
```

Optimized query:

```sql
SELECT id, type, title, message, createdAt
FROM notifications
WHERE studentID = $1 AND isRead = false
ORDER BY createdAt DESC
LIMIT 50;
```

The index turns the full table scan into an index scan directly on the student's rows. Adding `LIMIT 50` prevents unbounded result sets from being sent to the frontend. Computation cost drops from O(n) to roughly O(log n + k) where k is the matching rows.

### Is Adding Indexes on Every Column Safe?

No. Indexing every column is harmful:
- Each index adds write overhead — every INSERT or UPDATE must update all indexes.
- At 5M rows with frequent `is_read` updates, this becomes a serious bottleneck.
- Unused indexes waste disk space and confuse the query planner.

Only index columns that appear in `WHERE`, `ORDER BY`, or `JOIN` conditions with high selectivity. The composite index on `(studentID, isRead, createdAt DESC)` covers the exact query pattern needed.

### Find All Students Who Got a Placement Notification in the Last 7 Days

```sql
SELECT DISTINCT student_id
FROM notifications
WHERE notificationType = 'Placement'
AND createdAt >= NOW() - INTERVAL '7 days';
```

This query benefits from an index on `(notificationType, createdAt)`:

```sql
CREATE INDEX idx_notifications_type_created
ON notifications (notificationType, createdAt DESC);
```

---

# Stage 4

## Caching Strategy

### The Problem

Fetching notifications from DB on every page load for 50,000 students is unsustainable. Most students' notification feeds don't change between page loads. The DB is doing redundant work repeatedly.

### Strategy 1: Redis Cache per Student

Cache each student's unread notifications in Redis with a key like `notifications:unread:{studentId}`. Set a TTL of 60 seconds. On page load, check Redis first. On a cache miss, query the DB and repopulate.

**Trade-offs:**
- Pros: Near-zero DB load for hot students, sub-millisecond reads.
- Cons: Cache invalidation complexity — when a new notification arrives or a student marks one as read, the cache must be invalidated immediately. Stale reads within the TTL window are possible.

### Strategy 2: Server-Sent Events / WebSocket Push

Instead of the frontend polling on every page load, keep a persistent connection and push notifications in real time. The frontend maintains its own in-memory state. No DB query needed on page load.

**Trade-offs:**
- Pros: Zero page-load DB calls, true real-time, scales with connection pooling.
- Cons: Persistent connections consume server memory. Requires reconnection handling and a message broker (like Redis Pub/Sub) to fan out across multiple server instances.

### Strategy 3: Pagination + Cursor-based Loading

Return only the first page (e.g., 20 notifications) on load, load more on scroll. Combined with caching, this drastically reduces the data fetched per request.

**Trade-offs:**
- Pros: Consistent performance regardless of notification count.
- Cons: Requires frontend changes to support infinite scroll / load-more.

### Recommended Approach

Use Redis for caching unread counts and the first page of notifications, combined with WebSocket push to invalidate the cache instantly when new notifications arrive. This gives the best balance of performance and freshness.

---

# Stage 5

## Notify All — Analysis and Redesign

### The Original Pseudocode

```
function notify_all(student_ids: array, message: string):
    for student_id in student_ids:
        send_email(student_id, message)   # calls Email API
        save_to_db(student_id, message)   # DB insert
        push_to_app(student_id, message)  # WebSocket push
```

### Shortcomings

1. **Sequential loop** — with 50,000 students, this runs each action one at a time. If each iteration takes 100ms, the whole operation takes 83 minutes.
2. **No fault tolerance** — if `send_email` fails at student 200, the entire remaining list is abandoned. The 49,800 students after that get nothing.
3. **DB insert inside loop** — 50,000 individual INSERT statements instead of a bulk insert. Extremely slow and locks tables unnecessarily.
4. **Tight coupling** — email, DB, and push happen synchronously in the same thread. A slow email API blocks DB writes.

### The 200-Student Failure Scenario

When `send_email` fails at student 200, the function crashes out or silently skips the remaining students with no retry, no dead-letter queue, and no visibility into what happened.

### Redesigned Approach

Decouple the three operations using a **message queue** (e.g., Redis Queue, Celery, or RabbitMQ). The `notify_all` function becomes a dispatcher that enqueues jobs, not a sequential executor.

**Revised Pseudocode:**

```
function notify_all(student_ids: array, message: string):
    bulk_insert_to_db(student_ids, message)

    for chunk in split_into_chunks(student_ids, size=500):
        enqueue_job("send_email_batch", chunk, message)
        enqueue_job("push_notification_batch", chunk, message)

function send_email_batch(student_ids: array, message: string):
    for student_id in student_ids:
        result = send_email(student_id, message)
        if result.failed:
            push_to_dead_letter_queue(student_id, message)

function retry_dead_letter_queue():
    for item in dead_letter_queue:
        send_email(item.student_id, item.message)
```

**Should DB save and email send happen together?**

No. They should be independent. The DB insert is authoritative — it records that the notification exists regardless of delivery. Email is a best-effort delivery channel. If the email API is down, the student can still see the notification in-app. Coupling them means a flaky email provider causes data loss in the DB, which is far worse.

The DB bulk insert runs first (one statement, all 50,000 rows). Email and push are enqueued as async background jobs processed by workers. Failed emails go into a retry queue with exponential backoff.

---

# Stage 6

## Priority Inbox Implementation

### Approach

Notifications are scored using two dimensions:

1. **Type weight**: Placement = 3, Result = 2, Event = 1
2. **Recency**: Unix timestamp of `createdAt` (higher = more recent)

The priority score is a tuple `(type_weight, recency)` so that within the same type, newer notifications always rank higher. A min-heap of size `n` is maintained to extract the top `n` efficiently.

### Handling New Notifications Efficiently

As new notifications arrive (via WebSocket or polling), they are pushed into a **max-heap** maintained in memory. If the heap size exceeds the configured maximum, the lowest-priority item is evicted. This keeps the top-n list updated in O(log n) time per insertion without re-sorting the entire list.

### Why a Heap and Not a Sort?

Sorting the full notification list every time a new one arrives is O(m log m) where m is the total count. With 5M notifications, this is not viable. A heap gives O(log n) insertion and O(log n) extraction while always maintaining the correct top-n order.

### Code

The working implementation is in `notification_app_be/app.py`.

**Endpoint:** `GET /notifications/priority?n=10`

**Response:**
```json
{
  "top_n": 10,
  "returned": 10,
  "priority_order": "Placement > Result > Event, then by recency",
  "notifications": [
    {
      "id": "uuid",
      "type": "Placement",
      "message": "CSX Corporation hiring",
      "timestamp": "2026-04-22 17:51:18",
      "priorityScore": 3
    }
  ]
}
```

Screenshots of the output are included in the `notification_app_be/` folder.
