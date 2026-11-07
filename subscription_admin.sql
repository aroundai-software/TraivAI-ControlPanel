-- Adds the last-post columns. Run this once in the Traiv AI Supabase SQL editor.
-- Renew and stop already work from the subscription page. This only adds the last post.

alter table public.installs
  add column if not exists last_posted_at timestamptz,
  add column if not exists last_invoice text;

comment on column public.installs.last_posted_at is 'When this PC last posted a voucher.';
comment on column public.installs.last_invoice is 'Invoice number on that last post.';

create or replace function public.install_post_note(p_machine_id text, p_invoice text)
returns void
language plpgsql
security definer
set search_path = public
as $fn$
declare
  trimmed text := left(trim(coalesce(p_machine_id, '')), 80);
  invoice text := left(trim(coalesce(p_invoice, '')), 80);
begin
  if length(trimmed) < 8 then
    return;
  end if;
  update public.installs
    set last_posted_at = now(),
        last_invoice = nullif(invoice, '')
    where machine_id = trimmed;
end;
$fn$;

revoke all on function public.install_post_note(text, text) from public, anon, authenticated;
grant execute on function public.install_post_note(text, text) to anon, service_role;
