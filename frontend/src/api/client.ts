import axios from 'axios'

const api = axios.create({
  baseURL: '',
  headers: { 'Content-Type': 'application/json' },
  timeout: 30_000,
  withCredentials: true,
})

// Retry GET requests up to 2 times on transient failures
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const config = error.config
    if (
      config &&
      config.method === 'get' &&
      (!config._retryCount || config._retryCount < 2) &&
      (!error.response || error.response.status >= 500)
    ) {
      config._retryCount = (config._retryCount || 0) + 1
      const delay = config._retryCount * 1000
      await new Promise((r) => setTimeout(r, delay))
      return api(config)
    }

    // Extract a readable error message
    const message =
      error.response?.data?.detail ||
      error.response?.data?.message ||
      error.message ||
      '网络请求失败'

    // Attach readable message for consumers
    error.userMessage = message

    return Promise.reject(error)
  },
)

export default api
