"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { isClientApiError } from "@/lib/api";

const MAX_QUERY_RETRIES = 3;

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // A missing session or invalid ID (4xx) will not change on retry; fail
            // fast so pages can show their error state instead of a long spinner.
            retry: (failureCount, error) => !isClientApiError(error) && failureCount < MAX_QUERY_RETRIES,
          },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
