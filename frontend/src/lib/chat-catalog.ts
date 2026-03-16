import { defineSchema, defineCatalog } from "@json-render/core"
import { z } from "zod"

/**
 * json-render schema + catalog for all chat/agent surfaces.
 *
 * Components the agent can render:
 * - Message: text bubble (user or agent)
 * - ActionResult: result of an executed action
 * - QuestionForm: agent asks user a question
 * - ChoiceGroup: present choices to user
 * - Screenshot: embedded image
 * - LinkCard: rich link preview
 * - CodeBlock: syntax-highlighted code
 * - Card / Stack: layout primitives
 *
 * Actions:
 * - send_reply: user replies to agent
 * - select_choice: user picks from options
 * - click_link: user clicks a link card
 */

const schema = defineSchema((s) => ({
  spec: s.object({
    root: s.string(),
    elements: s.record(s.any()),
  }),
  catalog: s.object({
    components: s.map({
      props: s.zod(),
      hasChildren: s.boolean(),
    }),
    actions: s.map({
      params: s.zod(),
    }),
  }),
}))

export const chatCatalog = defineCatalog(schema, {
  components: {
    Message: {
      props: z.object({
        role: z.enum(["user", "agent"]),
        content: z.string(),
      }),
      hasChildren: false,
    },
    ActionResult: {
      props: z.object({
        action: z.string(),
        status: z.enum(["success", "error"]),
        detail: z.string().optional(),
      }),
      hasChildren: false,
    },
    QuestionForm: {
      props: z.object({
        question: z.string(),
        placeholder: z.string().optional(),
      }),
      hasChildren: true,
    },
    ChoiceGroup: {
      props: z.object({
        label: z.string(),
        options: z.array(z.string()),
      }),
      hasChildren: false,
    },
    Screenshot: {
      props: z.object({
        src: z.string(),
        alt: z.string().optional(),
      }),
      hasChildren: false,
    },
    LinkCard: {
      props: z.object({
        title: z.string(),
        url: z.string(),
        description: z.string().optional(),
      }),
      hasChildren: false,
    },
    CodeBlock: {
      props: z.object({
        code: z.string(),
        language: z.string().optional(),
      }),
      hasChildren: false,
    },
    Card: {
      props: z.object({
        title: z.string().optional(),
      }),
      hasChildren: true,
    },
    Stack: {
      props: z.object({
        direction: z.enum(["vertical", "horizontal"]).optional(),
      }),
      hasChildren: true,
    },
  },
  actions: {
    send_reply: { params: z.object({ text: z.string() }) },
    select_choice: { params: z.object({ choice: z.string() }) },
    click_link: { params: z.object({ url: z.string() }) },
  },
})

export type ChatCatalog = typeof chatCatalog
