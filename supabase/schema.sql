-- Restaurant Front Desk: database schema
-- Run this once in the Supabase SQL editor (project in the Sydney region).

create extension if not exists pgcrypto;

create table if not exists restaurants (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  twilio_number text unique not null,      -- the Twilio number calls are diverted to, e.g. +61400000000
  owner_phone text not null,               -- owner's mobile for alerts, e.g. +61411111111
  fact_sheet text not null default '',     -- hours, menu, allergens, parking, BYO... written by the owner
  max_covers_per_slot int not null default 40,
  slot_minutes int not null default 30,
  open_time time not null default '17:00',
  last_booking_time time not null default '21:00',
  closed_days int[] not null default '{}', -- 0 = Monday ... 6 = Sunday
  max_online_party int not null default 7, -- bigger groups go to the owner
  shadow_mode boolean not null default true, -- true = AI replies wait for staff approval
  created_at timestamptz not null default now()
);

create table if not exists bookings (
  id uuid primary key default gen_random_uuid(),
  restaurant_id uuid not null references restaurants(id) on delete cascade,
  name text not null,
  phone text not null,
  party_size int not null check (party_size > 0),
  starts_at timestamptz not null,
  status text not null default 'confirmed' check (status in ('confirmed', 'cancelled')),
  reminded boolean not null default false,
  created_at timestamptz not null default now()
);
create index if not exists bookings_restaurant_time on bookings (restaurant_id, starts_at);

create table if not exists messages (
  id uuid primary key default gen_random_uuid(),
  restaurant_id uuid not null references restaurants(id) on delete cascade,
  phone text not null,
  direction text not null check (direction in ('in', 'out', 'draft', 'alert')),
  body text not null,
  created_at timestamptz not null default now()
);
create index if not exists messages_restaurant_time on messages (restaurant_id, created_at desc);

-- The backend uses the service-role key, so lock the tables down for everyone else.
alter table restaurants enable row level security;
alter table bookings enable row level security;
alter table messages enable row level security;

-- Example restaurant for testing. Change the numbers to your own Twilio number and mobile.
-- insert into restaurants (name, twilio_number, owner_phone, fact_sheet)
-- values ('Test Kitchen', '+61400000000', '+61411111111',
--   'Open Tue-Sun 5pm-10pm. Closed Mondays. BYO wine, $5 corkage. Street parking on Smith St. '
--   'Vegetarian and vegan options. Gluten-free: butter chicken, dal, rice. Contains nuts: korma.');
