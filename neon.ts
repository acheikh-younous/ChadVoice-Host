import { defineConfig } from '@neon/config/v1';

// Private bucket: ONLY ChadVoice backend should read/write these files.
// Install @neon/config, then run `neon link` and `neon deploy`.
export default defineConfig({
  buckets: {
    'chadvoice-private': { access: 'private' },
  },
});
