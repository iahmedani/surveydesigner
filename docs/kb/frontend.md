# Frontend (`react-ui/`)

React 18.2 · TypeScript 5.2 (strict) · Vite 5 · Redux Toolkit 1.9 · react-router 6 ·
axios · **@wfp/react** (WFP design system — use it first) · react-hook-form ·
react-beautiful-dnd · i18next · Sentry · MS Clarity (consent-gated).
**pnpm only** (`preinstall` enforces it). Prettier: double quotes, semicolons, width 80.

## Env & build
- `VITE_APP_API_ENDPOINT` (backend origin, e.g. `http://localhost:8080`),
  `VITE_BUILD_ID`, `VITE_CLARITY_PROJECT_ID`. Copy `.env.sample` → `.env`.
- `pnpm build` → `react-ui/dist/`, deployed separately from Django.
- Dev server: `pnpm dev` on :3000. Backend must allow it in `CORS_ALLOWED_ORIGINS`.

## Structure (`src/`)
```
main.tsx                 Provider + Suspense + i18n bootstrap
app/index.tsx            App: Sentry (prod), axios interceptors, routes, PrivateRoute
app/components/<Name>/   feature components (index.tsx, *.test.tsx, __snapshots__)
app/views/<Name>/        route-level pages (APIKeys, Faq, Home, SharedLink, Error)
app/redux/{store,actions,reducers}   RTK slices + createAsyncThunk actions
app/contexts/ModulesContext.tsx      mutable ref for wizard ordering/collapse state
app/utils/               API client, generate/download, error parsing, admin URLs
app/types/               api.d.ts, window.d.ts, @wfp type patches
locales/<lang>/translations.json + i18n.ts
```

## Routes (`app/index.tsx`)
| Path | View | Auth |
|---|---|---|
| `/` | `views/Home` → LandingPage or redirect to `/design/survey` | public |
| `/design/:step` (`survey`, `modules`, `review`, `generate`) | `components/SurveyWizard` | private |
| `/api-keys` | `views/APIKeys` | private |
| `/help` | `views/Faq` (markdown from `frontend-content` key `FAQmain`) | private |
| `/survey/copy/:uuid` | `views/SharedLink` | private |
| `*` | NotFound | public |

## API client & auth
- `utils/index.tsx` exports `API` (axios, `baseURL = VITE_APP_API_ENDPOINT + "/api/"`,
  `withCredentials: true`). **Always use `API`.**
- Session auth via cookies. Mutations must send `X-CSRFToken` from the `csrftoken`
  cookie (currently copy-pasted per call — prefer a shared helper for new code).
- An interceptor adds `Survey-Designer-Organizations` from `surveyForm.organizations`
  and `Accept-Language` from i18n. **If data is unexpectedly empty, check this header.**
- Session state: `GET /api/accounts/user/` (`loadUserData`) → `auth.is_logged`.

## State (Redux slices, `redux/reducers/`)
Data slices are `{isLoading, error, data}`: `modules`, `submodules`(+`selectedOptions`),
`indicators`, `indicatorAreas` (grouped client-side; area-less → "Other", id 0),
`surveys`, `projects`, `organizations`, `frontendContent`, `savedSurveys`.
Plus `app`, `notification` (global toast: kind/title/msg/show), `auth`,
**`surveyForm`** (the survey being built — name, orgs, category/type/mode,
attributes, submodules, indicators, sub_questions, orderings, languages),
`docFetcher` (Word job polling). Thunks in `redux/actions/*Actions.tsx`; use
`createAppAsyncThunk`, `useAppDispatch`, `useAppSelector` from `redux/store.tsx`.

## Wizard flow (`components/SurveyWizard`)
0. Saved surveys table (edit / delete / share).
1. **survey** (`components/Surveys`): title, orgs, category, type, mode, context.
   Changing criteria clears selections (`moduleCriteria.ts`).
2. **modules** (`components/Modules`): modules/submodules/indicators, drag ordering
   (`ordering.ts`), mandatory-indicator validation.
3. **review** (`components/Review`): extra sub-questions (`selection.ts`), languages,
   Enketo preview (`POST /preview/`).
4. **generate** (`components/Generate`): XLSForm download (`utils/generate.tsx:getXLS`,
   reads `X-Survey-Validation-Warnings`), Word export (`DocFetcher` polls every 5s),
   publish (`POST /upload/`), save (`post/putSavedSurvey`).
Errors from validation are parsed by `utils/apiError.tsx` (handles Blob bodies).

## i18n
- Locales: `en, fr, es, ar, pt, ru` in `src/locales/<lang>/translations.json`
  (bundled statically). **Add keys to `en` and every other locale** (non-English
  files currently lack `modules.errors.mandatoryIndicator`).
- Use `const { t } = useTranslation()`; don't hardcode user-facing strings.
- `pnpm translate` is **broken** (script lives in `dj-be/translationcli.py` with a
  stale path). Translate manually or fix the script first.
- Arabic has no RTL handling yet.

## Testing
vitest + jsdom + Testing Library; snapshot + behaviour tests beside components.
Use `createTestStore()` for Redux. Playwright e2e in `e2e/` — see [testing.md](testing.md).
