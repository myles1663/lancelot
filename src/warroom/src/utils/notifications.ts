export type WarRoomNotificationPriority = 'normal' | 'high'

export interface WarRoomNotificationDetail {
  message: string
  priority?: WarRoomNotificationPriority
}

const WAR_ROOM_NOTIFICATION_EVENT = 'warroom:notification'

function normalizePriority(priority?: string): WarRoomNotificationPriority {
  return priority === 'high' ? 'high' : 'normal'
}

export function emitWarRoomNotification(
  message: string,
  priority: WarRoomNotificationPriority = 'normal',
) {
  window.dispatchEvent(new CustomEvent<WarRoomNotificationDetail>(
    WAR_ROOM_NOTIFICATION_EVENT,
    {
      detail: {
        message,
        priority,
      },
    },
  ))
}

export function onWarRoomNotification(
  handler: (detail: Required<WarRoomNotificationDetail>) => void,
) {
  const listener = (event: Event) => {
    const customEvent = event as CustomEvent<WarRoomNotificationDetail>
    const message = customEvent.detail?.message
    if (!message) {
      return
    }

    handler({
      message,
      priority: normalizePriority(customEvent.detail?.priority),
    })
  }

  window.addEventListener(WAR_ROOM_NOTIFICATION_EVENT, listener)
  return () => window.removeEventListener(WAR_ROOM_NOTIFICATION_EVENT, listener)
}
