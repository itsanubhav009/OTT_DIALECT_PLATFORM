# Known Limitations

1. **File-Based Viewer State**: Viewer state is saved as JSON in `sample_run/viewer_state/<user_id>.json` for zero-dependency reproducibility. At 10M+ users, state would reside in Redis/DynamoDB partitioned by `user_id`.
2. **Conservative Messaging**: The planner prioritizes multi-row personalised `home_page` updates (`120m` TTL) and only triggers `whatsapp` templates when a live home page is already active, avoiding notification opt-outs.
3. **Rule-Based Genre Scoring**: Within a dialect, titles are ranked by unfinished series progress, historical genre frequency, and release recency rather than collaborative filtering embeddings.
