import { useCallback } from "react";
import { ChatWidget } from "@/features/assistant/ChatWidget";
import {
  useAdminAssistantChatMutation,
  useAdminAssistantStatusQuery,
  useCustomerAssistantChatMutation,
  useCustomerAssistantStatusQuery,
} from "@/features/assistant/assistantApi";
import { canManage } from "@/features/restaurant/nav";
import {
  useAssistantAskMutation,
  useAssistantDoMutation,
  useStaffMeQuery,
  type ChatTurn,
} from "@/features/restaurant/restaurantApi";

/**
 * The widget, wired up for each of the three places it lives. Each decides
 * only whether it is shown and which endpoint it talks to; what it may do is
 * decided on the server.
 */

/** The restaurant dashboard. Owners and managers: the people the menu
 *  screens it drives are for. Works without a model -- the rules answer
 *  what they know -- so it is not gated on one. */
export function ManageAssistant() {
  const role = useStaffMeQuery().data?.role_code ?? null;
  const [ask] = useAssistantAskMutation();
  const [act] = useAssistantDoMutation();

  const send = useCallback(
    (text: string, history: ChatTurn[]) => ask({ text, history }).unwrap(),
    [ask],
  );
  const confirm = useCallback(
    async (proposal: Record<string, unknown>) => (await act({ proposal }).unwrap()).text,
    [act],
  );

  if (!canManage(role)) return null;
  return (
    <ChatWidget
      storageKey="ze:assistant:manage"
      title="Assistant"
      greeting="Ask about your menu and orders, or tell me what to change. I'll show you any change before making it."
      examples={["what's sold out?", "we're out of fries", "change the price of fries to 2.50", "how many orders today"]}
      placeholder="we're out of fries"
      footnote="Refunds and staff changes stay on their own screens."
      send={send}
      confirm={confirm}
      voice
      aboveMobileBar
    />
  );
}

/** The platform admin. Read only, and only when a model is configured:
 *  there are no rules on this side to answer without one. */
export function AdminAssistant() {
  const enabled = useAdminAssistantStatusQuery().data?.enabled ?? false;
  const [chat] = useAdminAssistantChatMutation();
  const send = useCallback(
    (text: string, history: ChatTurn[]) => chat({ text, history }).unwrap(),
    [chat],
  );

  if (!enabled) return null;
  return (
    <ChatWidget
      storageKey="ze:assistant:admin"
      title="ZenoEats assistant"
      greeting="Ask about your restaurants, their orders, sales and Stripe status."
      examples={["which restaurants are live?", "sales this week", "latest orders at Jr's Corner"]}
      placeholder="sales this month"
      footnote="Reads only. Changes stay on the admin screens."
      send={send}
    />
  );
}

/** The storefront. A helper for customers, when a model is configured. */
export function CustomerAssistant({ restaurantName }: { restaurantName: string }) {
  const enabled = useCustomerAssistantStatusQuery().data?.enabled ?? false;
  const [chat] = useCustomerAssistantChatMutation();
  const send = useCallback(
    (text: string, history: ChatTurn[]) => chat({ text, history }).unwrap(),
    [chat],
  );

  if (!enabled) return null;
  return (
    <ChatWidget
      storageKey="ze:assistant:customer"
      title={`Ask ${restaurantName}`}
      greeting={`Hi! Ask me anything about ${restaurantName}'s menu, opening times, pickup or delivery, or your order.`}
      examples={["what do you recommend?", "do you deliver?", "where's my order?"]}
      placeholder="Ask a question"
      footnote="For allergies, please call the restaurant."
      send={send}
      aboveMobileBar
    />
  );
}
