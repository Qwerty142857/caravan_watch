-- ============================================================================
-- Static Caravan Watch — private listings schema
--
-- Run this once in the Supabase dashboard: SQL Editor -> New query -> paste ->
-- Run. It is safe to re-run: everything is created with "if not exists" or
-- dropped first.
--
-- The security model, in one line: the browser may only ever read live
-- listings and write its OWN draft, and nothing the browser can do will mark a
-- listing as paid. Only the Stripe webhook (running server-side with the
-- service_role key, which bypasses RLS) can do that.
-- ============================================================================


-- ---------------------------------------------------------------- types ----
do $$
begin
  if not exists (select 1 from pg_type where typname = 'listing_status') then
    create type public.listing_status as enum (
      'draft',            -- being written, only the owner can see it
      'pending_payment',  -- sent to Stripe Checkout, waiting on the webhook
      'active',           -- paid for and publicly visible until paid_until
      'expired',          -- was live, the paid period ran out
      'rejected'          -- taken down by you
    );
  end if;
end
$$;


-- ------------------------------------------------------------- profiles ----
-- One row per account. auth.users holds the email and password hash and is
-- managed by Supabase — never copy passwords or tokens into your own tables.
create table if not exists public.profiles (
  id           uuid primary key references auth.users (id) on delete cascade,
  display_name text,
  contact_name text,
  -- how buyers should get in touch; shown on the listing, so it is deliberately
  -- separate from the private account email in auth.users
  contact_email text,
  contact_phone text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);


-- ------------------------------------------------------------- listings ----
-- Mirrors the shape of a data.json row so the listings pages can merge private
-- listings straight into the scraped ones.
create table if not exists public.listings (
  id            uuid primary key default gen_random_uuid(),
  owner         uuid not null default auth.uid() references auth.users (id) on delete cascade,

  -- the caravan
  name          text,
  make          text,
  model         text,
  year          integer,
  bedrooms      integer,
  dimensions    text,
  price_number  numeric(10, 2),
  description   text,
  photo_paths   text[] not null default '{}',

  -- where it is; park_name should match parks.json's park_name where possible
  park_name     text,
  town          text,
  county        text,
  region        text,
  postcode      text,

  -- lifecycle — only the service_role may write these two (see the revokes)
  status        public.listing_status not null default 'draft',
  paid_until    timestamptz,

  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),

  constraint listings_year_sane      check (year is null or (year between 1950 and 2100)),
  constraint listings_bedrooms_sane  check (bedrooms is null or (bedrooms between 0 and 12)),
  constraint listings_price_sane     check (price_number is null or price_number >= 0),
  constraint listings_photo_limit    check (array_length(photo_paths, 1) is null
                                            or array_length(photo_paths, 1) <= 12)
);

create index if not exists listings_owner_idx  on public.listings (owner);
create index if not exists listings_live_idx   on public.listings (status, paid_until);
create index if not exists listings_region_idx on public.listings (region);
create index if not exists listings_park_idx   on public.listings (park_name);


-- ------------------------------------------------------------- payments ----
-- Written only by the Stripe webhook. Users can read their own rows so
-- "my listings" can show a receipt; nobody but the webhook can write one.
create table if not exists public.payments (
  id                       uuid primary key default gen_random_uuid(),
  listing_id               uuid references public.listings (id) on delete set null,
  owner                    uuid references auth.users (id) on delete set null,
  stripe_checkout_session  text unique,
  stripe_payment_intent    text,
  amount_pence             integer not null,
  currency                 text not null default 'gbp',
  status                   text not null default 'pending',  -- pending | paid | refunded | failed
  created_at               timestamptz not null default now()
);

create index if not exists payments_owner_idx   on public.payments (owner);
create index if not exists payments_listing_idx on public.payments (listing_id);


-- ------------------------------------------------- updated_at bookkeeping ----
create or replace function public.touch_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists profiles_touch on public.profiles;
create trigger profiles_touch before update on public.profiles
  for each row execute function public.touch_updated_at();

drop trigger if exists listings_touch on public.listings;
create trigger listings_touch before update on public.listings
  for each row execute function public.touch_updated_at();


-- ------------------------------------------ a profile for every new user ----
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, coalesce(new.raw_user_meta_data ->> 'display_name',
                           split_part(new.email, '@', 1)))
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();


-- ============================================================================
-- Row Level Security
-- Without these policies the tables are readable by nobody; with them the
-- database itself enforces who sees and writes what, so a hostile browser
-- cannot help itself to other people's data.
-- ============================================================================
alter table public.profiles enable row level security;
alter table public.listings enable row level security;
alter table public.payments enable row level security;

-- ---- profiles: yours and only yours -----------------------------------------
drop policy if exists "profiles are self-service" on public.profiles;
create policy "profiles are self-service" on public.profiles
  for all to authenticated
  using (id = auth.uid())
  with check (id = auth.uid());

-- ---- listings ---------------------------------------------------------------
-- Anyone, signed in or not, sees listings that are paid for and still in date.
drop policy if exists "live listings are public" on public.listings;
create policy "live listings are public" on public.listings
  for select to anon, authenticated
  using (status = 'active' and paid_until > now());

-- An owner additionally sees their own, whatever state it is in.
drop policy if exists "owners see their own listings" on public.listings;
create policy "owners see their own listings" on public.listings
  for select to authenticated
  using (owner = auth.uid());

-- You may only create a draft, and only in your own name.
drop policy if exists "owners create drafts" on public.listings;
create policy "owners create drafts" on public.listings
  for insert to authenticated
  with check (owner = auth.uid() and status = 'draft');

-- You may edit your own listing while it is a draft or awaiting payment.
-- Editing a live listing is deliberately not allowed from the browser: let it
-- lapse, or handle edits server-side, so paid-for copy cannot be swapped out.
drop policy if exists "owners edit unpublished listings" on public.listings;
create policy "owners edit unpublished listings" on public.listings
  for update to authenticated
  using (owner = auth.uid() and status in ('draft', 'pending_payment'))
  with check (owner = auth.uid());

drop policy if exists "owners delete their own listings" on public.listings;
create policy "owners delete their own listings" on public.listings
  for delete to authenticated
  using (owner = auth.uid());

-- ---- payments: read your own, write none ------------------------------------
drop policy if exists "owners read their own payments" on public.payments;
create policy "owners read their own payments" on public.payments
  for select to authenticated
  using (owner = auth.uid());


-- ============================================================================
-- Column privileges — the belt to the RLS braces.
-- RLS says WHICH ROWS you may touch; these say WHICH COLUMNS. Without them an
-- owner could update their own draft and set status = 'active' themselves,
-- publishing without paying. service_role ignores all of this, which is why the
-- Stripe webhook can still do its job.
-- ============================================================================
revoke update (owner, status, paid_until, created_at) on public.listings from anon, authenticated;
revoke insert, update, delete on public.payments from anon, authenticated;
revoke all on public.payments from anon;


-- ============================================================================
-- A clean public shape for the listings pages.
-- security_invoker = true makes the view obey the caller's RLS rather than the
-- view owner's — without it a view is a hole straight through your policies.
-- ============================================================================
drop view if exists public.public_listings;
create view public.public_listings
with (security_invoker = true)
as
select
  l.id,
  l.name,
  l.make,
  l.model,
  l.year,
  l.bedrooms,
  l.dimensions,
  l.price_number,
  l.description,
  l.photo_paths,
  l.park_name,
  l.town,
  l.county,
  l.region,
  l.postcode,
  l.paid_until,
  l.created_at,
  p.display_name  as seller_name,
  p.contact_email as seller_email,
  p.contact_phone as seller_phone
from public.listings l
left join public.profiles p on p.id = l.owner
where l.status = 'active' and l.paid_until > now();

grant select on public.public_listings to anon, authenticated;

-- The view reads profiles, which is owner-only under RLS, so grant exactly the
-- one thing the public needs: the seller's chosen contact details on a listing
-- that is live. Nothing else in profiles is reachable.
drop policy if exists "seller contact details on live listings" on public.profiles;
create policy "seller contact details on live listings" on public.profiles
  for select to anon, authenticated
  using (exists (
    select 1 from public.listings l
    where l.owner = profiles.id
      and l.status = 'active'
      and l.paid_until > now()
  ));


-- ============================================================================
-- Photo storage
-- Files live at listing-photos/<user id>/<listing id>/<file>, and the first
-- path segment must be the uploader's own id — so nobody can write into or
-- delete from anyone else's folder.
-- ============================================================================
insert into storage.buckets (id, name, public)
values ('listing-photos', 'listing-photos', true)
on conflict (id) do nothing;

drop policy if exists "listing photos are public" on storage.objects;
create policy "listing photos are public" on storage.objects
  for select to anon, authenticated
  using (bucket_id = 'listing-photos');

drop policy if exists "owners upload their own photos" on storage.objects;
create policy "owners upload their own photos" on storage.objects
  for insert to authenticated
  with check (bucket_id = 'listing-photos'
              and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists "owners replace their own photos" on storage.objects;
create policy "owners replace their own photos" on storage.objects
  for update to authenticated
  using (bucket_id = 'listing-photos'
         and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists "owners delete their own photos" on storage.objects;
create policy "owners delete their own photos" on storage.objects
  for delete to authenticated
  using (bucket_id = 'listing-photos'
         and (storage.foldername(name))[1] = auth.uid()::text);


-- ============================================================================
-- Housekeeping: flip lapsed listings from 'active' to 'expired'.
-- Not required for correctness — every read already checks paid_until > now() —
-- but it keeps "my listings" honest. Call it from a scheduled job, or run it by
-- hand now and then. Runs as its owner so it can write the protected columns.
-- ============================================================================
create or replace function public.expire_listings()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  n integer;
begin
  update public.listings
     set status = 'expired'
   where status = 'active'
     and paid_until <= now();
  get diagnostics n = row_count;
  return n;
end;
$$;

revoke execute on function public.expire_listings() from anon, authenticated;
