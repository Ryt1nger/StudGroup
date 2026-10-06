// This file is generated from packages/contracts/openapi.yaml by scripts/generate-examples.mjs.
// Do not edit by hand.

export const contractExamples = {
  "HomeworkListEmpty": {
    "server_time": "2026-10-05T06:00:00Z",
    "generated_at": "2026-10-05T06:00:00Z",
    "group_timezone": "Europe/Moscow",
    "freshness": {
      "last_successful_sync_at": "2026-10-05T06:00:00Z",
      "stale": false
    },
    "processing": {
      "state": "idle",
      "retry_after_seconds": null
    },
    "items": [],
    "next_cursor": null
  },
  "SchedulePopulated": {
    "generated_at": "2026-10-05T06:00:00Z",
    "group_timezone": "Europe/Moscow",
    "start": "2026-10-05",
    "end": "2026-10-11",
    "week_state": "ready",
    "first_week_anchor": "2026-10-05",
    "selected_week": "first",
    "lessons": [
      {
        "id": "55555555-5555-4555-8555-555555555555:2026-10-05",
        "subject": "Математический анализ",
        "starts_at": "2026-10-05T09:00:00+03:00",
        "ends_at": "2026-10-05T10:30:00+03:00",
        "teacher": "Иванов А. С.",
        "location": "Ауд. 320",
        "status": "scheduled"
      }
    ]
  },
  "ScheduleEmpty": {
    "generated_at": "2026-10-05T06:00:00Z",
    "group_timezone": "Europe/Moscow",
    "start": "2026-10-05",
    "end": "2026-10-11",
    "week_state": "ready",
    "first_week_anchor": null,
    "selected_week": null,
    "lessons": []
  },
  "ScheduleNeedsClarification": {
    "generated_at": "2026-10-05T06:00:00Z",
    "group_timezone": "Europe/Moscow",
    "start": "2026-10-05",
    "end": "2026-10-11",
    "week_state": "needs_clarification",
    "first_week_anchor": null,
    "selected_week": null,
    "lessons": []
  },
  "SessionActive": {
    "access_token": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    "token_type": "Bearer",
    "expires_at": "2026-10-05T00:00:00Z",
    "session": {
      "user": {
        "id": "11111111-1111-4111-8111-111111111111",
        "telegram_user_id": 123456789,
        "display_name": "Анна",
        "username": "anna_student"
      },
      "access_state": "active",
      "membership": {
        "id": "22222222-2222-4222-8222-222222222222",
        "role": "student",
        "status": "active",
        "next_check_at": null,
        "can_recheck": false
      },
      "group": {
        "id": "33333333-3333-4333-8333-333333333333",
        "name": "ИВТ-21",
        "timezone": "Europe/Moscow",
        "status": "active",
        "pilot_authorized": true
      },
      "permissions": [
        "today.read",
        "homework.read",
        "homework.completion.write",
        "schedule.read"
      ],
      "server_time": "2026-10-04T12:00:00Z"
    }
  },
  "SessionNoActiveGroup": {
    "access_token": "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
    "token_type": "Bearer",
    "expires_at": "2026-10-05T00:00:00Z",
    "session": {
      "user": {
        "id": "11111111-1111-4111-8111-111111111111",
        "telegram_user_id": 123456789,
        "display_name": "Анна",
        "username": null
      },
      "access_state": "no_active_group",
      "membership": null,
      "group": null,
      "permissions": [],
      "server_time": "2026-10-04T12:00:00Z"
    }
  },
  "TodayPopulated": {
    "generated_at": "2026-10-04T12:00:00Z",
    "server_time": "2026-10-04T12:00:00Z",
    "group_timezone": "Europe/Moscow",
    "next_lesson": {
      "state": "upcoming",
      "lesson": {
        "id": "55555555-5555-4555-8555-555555555555:2026-10-05",
        "subject": "Математический анализ",
        "starts_at": "2026-10-05T09:00:00+03:00",
        "ends_at": "2026-10-05T10:30:00+03:00",
        "teacher": "Иванов А. С.",
        "location": "Ауд. 320",
        "status": "scheduled"
      }
    },
    "freshness": {
      "last_successful_sync_at": "2026-10-04T11:58:00Z",
      "stale": false
    },
    "processing": {
      "state": "idle",
      "retry_after_seconds": null
    },
    "empty": false,
    "sections": [
      {
        "kind": "due_today",
        "items": [
          {
            "id": "44444444-4444-4444-8444-444444444444",
            "type": "homework",
            "title": "ДЗ №12–140",
            "subject": {
              "id": "55555555-5555-4555-8555-555555555555",
              "name": "Математический анализ"
            },
            "summary": "Решить задания из методички",
            "deadline": {
              "state": "known",
              "at": "2026-10-04T20:59:00Z",
              "date_only": false
            },
            "status": "published",
            "urgency": "urgent",
            "visibility": "group",
            "verification_state": "from_group_message",
            "revision": 3,
            "source": {
              "kind": "telegram_group_message",
              "imported": false,
              "availability": {
                "state": "available",
                "reason": null
              }
            },
            "processing": {
              "state": "none",
              "retry_after_seconds": null
            },
            "significant_update": false,
            "significant_updated_at": null,
            "my_state": {
              "completion": "pending",
              "completed_at": null,
              "completed_revision": null
            },
            "created_at": "2026-10-04T09:20:00Z",
            "updated_at": "2026-10-04T09:20:00Z"
          }
        ]
      }
    ]
  },
  "TodayEmpty": {
    "generated_at": "2026-10-04T12:00:00Z",
    "server_time": "2026-10-04T12:00:00Z",
    "group_timezone": "Europe/Moscow",
    "freshness": {
      "last_successful_sync_at": "2026-10-04T11:58:00Z",
      "stale": false
    },
    "processing": {
      "state": "idle",
      "retry_after_seconds": null
    },
    "empty": true,
    "sections": []
  },
  "TodayDelayed": {
    "generated_at": "2026-10-04T12:00:00Z",
    "server_time": "2026-10-04T12:00:00Z",
    "group_timezone": "Europe/Moscow",
    "freshness": {
      "last_successful_sync_at": "2026-10-04T11:40:00Z",
      "stale": true
    },
    "processing": {
      "state": "delayed",
      "retry_after_seconds": 30
    },
    "empty": true,
    "sections": []
  },
  "HomeworkDetailAvailable": {
    "id": "44444444-4444-4444-8444-444444444444",
    "type": "homework",
    "title": "ДЗ №12–140",
    "subject": {
      "id": "55555555-5555-4555-8555-555555555555",
      "name": "Математический анализ"
    },
    "summary": "Решить задания из методички",
    "deadline": {
      "state": "known",
      "at": "2026-10-04T20:59:00Z",
      "date_only": false
    },
    "status": "published",
    "urgency": "urgent",
    "visibility": "group",
    "verification_state": "from_group_message",
    "revision": 3,
    "source": {
      "kind": "telegram_group_message",
      "imported": false,
      "availability": {
        "state": "available",
        "reason": null
      }
    },
    "processing": {
      "state": "none",
      "retry_after_seconds": null
    },
    "significant_update": false,
    "significant_updated_at": null,
    "my_state": {
      "completion": "pending",
      "completed_at": null,
      "completed_revision": null
    },
    "created_at": "2026-10-04T09:20:00Z",
    "updated_at": "2026-10-04T09:20:00Z",
    "description": "Решить задания №12–140 и оформить ответ в PDF.",
    "source_detail": {
      "kind": "telegram_group_message",
      "imported": false,
      "availability": {
        "state": "available",
        "reason": null
      },
      "message_date": "2026-10-04T09:15:00Z",
      "excerpt": "По матану решаем номера 12–140.",
      "action": {
        "type": "open_telegram_link",
        "url": "https://t.me/c/1234567890/42"
      }
    },
    "permissions": [
      "homework.read",
      "homework.completion.write"
    ],
    "generated_at": "2026-10-04T12:00:00Z"
  },
  "HomeworkDetailUnavailable": {
    "id": "66666666-6666-4666-8666-666666666666",
    "type": "homework",
    "title": "Подготовить презентацию",
    "subject": {
      "id": "77777777-7777-4777-8777-777777777777",
      "name": "Маркетинг"
    },
    "summary": "Точная дата уточняется",
    "deadline": {
      "state": "unknown",
      "at": null,
      "date_only": false
    },
    "status": "needs_clarification",
    "urgency": "normal",
    "visibility": "group",
    "verification_state": "imported",
    "revision": 1,
    "source": {
      "kind": "imported_history",
      "imported": true,
      "availability": {
        "state": "unavailable",
        "reason": "imported_without_link"
      }
    },
    "processing": {
      "state": "none",
      "retry_after_seconds": null
    },
    "significant_update": false,
    "significant_updated_at": null,
    "my_state": {
      "completion": "pending",
      "completed_at": null,
      "completed_revision": null
    },
    "created_at": "2026-09-20T10:00:00Z",
    "updated_at": "2026-09-20T10:00:00Z",
    "description": "Подготовить презентацию; срок в исходной переписке не указан.",
    "source_detail": {
      "kind": "imported_history",
      "imported": true,
      "availability": {
        "state": "unavailable",
        "reason": "imported_without_link"
      },
      "message_date": "2026-09-20T09:55:00Z",
      "excerpt": null,
      "action": {
        "type": "none",
        "url": null
      }
    },
    "permissions": [
      "homework.read",
      "homework.completion.write"
    ],
    "generated_at": "2026-10-04T12:00:00Z"
  }
} as const;

export type ContractExampleName = keyof typeof contractExamples;
