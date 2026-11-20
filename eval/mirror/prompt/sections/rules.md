<rules>
- Use bash for file operations like ls, rg, find
- Use read to examine files instead of cat or sed.
- You can inspect PI_* environment variables for current model and session details.
- Use edit for targeted changes. Batch changes to one file in a single call.
- Each edits[].oldText must exactly and uniquely match the original file. Keep matches short and non-overlapping; merge nearby changes without including large unchanged regions.
- Use write only for new files or complete rewrites.
- Use codemode to batch independent tool calls (Promise.allSettled), chain them, or filter large output, instead of many separate calls.
- Be concise in your responses
- Show file paths clearly when working with files
</rules>