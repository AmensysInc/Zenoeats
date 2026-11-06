import { api } from "@/services/api";
import type { ChatTurn } from "@/features/restaurant/restaurantApi";

/**
 * The chat on the platform admin and on the storefront. Both read only; the
 * restaurant dashboard's assistant, which proposes changes, is in
 * restaurantApi.
 *
 * `status` says whether to show the chat at all: both need a model, and a
 * button that opens onto "not switched on" is worse than no button.
 */
export const assistantApi = api.injectEndpoints({
  endpoints: (build) => ({
    adminAssistantStatus: build.query<{ enabled: boolean }, void>({
      query: () => ({ url: "/admin/assistant/status" }),
    }),
    adminAssistantChat: build.mutation<{ text: string }, { text: string; history: ChatTurn[] }>({
      query: (body) => ({ url: "/admin/assistant/chat", method: "POST", body }),
    }),
    customerAssistantStatus: build.query<{ enabled: boolean }, void>({
      query: () => ({ url: "/assistant/status" }),
    }),
    customerAssistantChat: build.mutation<{ text: string }, { text: string; history: ChatTurn[] }>({
      query: (body) => ({ url: "/assistant/chat", method: "POST", body }),
    }),
  }),
});

export const {
  useAdminAssistantStatusQuery,
  useAdminAssistantChatMutation,
  useCustomerAssistantStatusQuery,
  useCustomerAssistantChatMutation,
} = assistantApi;
