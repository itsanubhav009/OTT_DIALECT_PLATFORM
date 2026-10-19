# Known Limitations

1. **JSON File State Store vs. Distributed Key-Value Store**:
   - Durable viewer state is written to `sample_run/viewer_state/<user_id>.json` for zero-dependency evaluation. For 10M+ users, state would be stored in Redis/DynamoDB partitioned by `user_id`.
2. **Conservative Messaging Frequency**:
   - To avoid notification opt-outs (which the simulator penalizes on noisy pushes), the planner prioritizes high-impact personalised `home_page` layouts (with `next_episode`, `new_releases`, and `popular_in_dialect` rows) and reserves `whatsapp` template nudges for users who already have a live home page within the 120-minute TTL.
3. **Static Genre Affinity Weighting**:
   - Title ranking within a dialect uses historical play genre counts, in-progress `content_slug` matching, and premium status rather than neural collaborative filtering embeddings.
