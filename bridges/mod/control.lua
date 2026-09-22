-- A body for Bonsai, and the per-tick code needed to move it.
--
-- RCON can run any Lua, which is enough to read the world and to build in it, but
-- not to walk: walking_state is an input, the game clears it every tick, so a console
-- command moves the character exactly one tick's worth - 0.15 tiles - and then it
-- stops. Walking has to be driven from on_tick, and on_tick only exists in a mod.
--
-- The visible half matters as much as the moving half. A character created from the
-- console has no player attached, so it has no name above it and nothing on the map,
-- and watching it is guessing. This draws the name and keeps a map marker on it.

local ARRIVED = 2.0          -- tiles; closer than this counts as being there
local NODE_REACHED = 1.2     -- tiles; how near a waypoint has to be to move on
local STUCK_TICKS = 180      -- 3 seconds of not getting closer is stuck, not slow

local DIRECTIONS = {
  [0] = defines.direction.east,      [1] = defines.direction.southeast,
  [2] = defines.direction.south,     [3] = defines.direction.southwest,
  [4] = defines.direction.west,      [5] = defines.direction.northwest,
  [6] = defines.direction.north,     [7] = defines.direction.northeast,
}

local function direction_towards(from, to)
  local angle = math.atan2(to.y - from.y, to.x - from.x)
  local slice = math.floor((angle / (math.pi / 4)) + 0.5) % 8
  return DIRECTIONS[slice]
end

local function distance(a, b)
  local dx, dy = a.x - b.x, a.y - b.y
  return math.sqrt(dx * dx + dy * dy)
end

-- The goal deliberately outlives the walk. Clearing it on arrival made `remaining`
-- read 0 and `arrived` read false at the same moment, which told the bridge nothing
-- and told it confidently.
local function stop_walking(character)
  if character and character.valid then
    character.walking_state = {walking = false, direction = defines.direction.north}
  end
  storage.path, storage.node = nil, nil
end

local function label(character)
  -- 2.0 returns a render object that destroys itself; rendering.destroy(id) is gone.
  if storage.tag and storage.tag.valid then storage.tag.destroy() end
  storage.tag = rendering.draw_text{
    text = storage.name or "Bonsai",
    surface = character.surface,
    target = {entity = character, offset = {0, -2.2}},
    color = {r = 0.45, g = 1.0, b = 0.65},
    scale = 1.3,
    alignment = "center",
  }
end

local function ensure_body()
  if storage.bot and storage.bot.valid then return storage.bot end
  local surface = game.surfaces.nauvis
  -- Nothing is destroyed here any more. This used to clear "stray" characters so
  -- that "the character on this surface" meant Bonsai's - and a player's character
  -- is a character entity, so it deleted someone playing alongside, and everything
  -- they were carrying, the moment this ran. Identifying the body by unit number
  -- instead removed the reason to delete anything, and a guard that only spares
  -- characters with a player attached would still be one API detail away from doing
  -- it again. Strays are harmless; nothing looks them up.
  local where = surface.find_non_colliding_position("character", {0, 0}, 64, 1) or {0, 0}
  local character = surface.create_entity{name = "character", position = where,
                                          force = "player"}
  character.color = {r = 0.32, g = 0.86, b = 0.46}
  storage.bot = character
  label(character)
  return character
end

-- The map marker is moved rather than redrawn, so the map does not flicker, and only
-- when it has actually gone somewhere - a tag rewritten every tick is a tag that
-- cannot be clicked.
local function update_marker(character)
  local force = game.forces.player
  local position = character.position
  if storage.marker and storage.marker.valid then
    if distance(storage.marker.position, position) < 12 then return end
    storage.marker.destroy()
    storage.marker = nil
  end
  -- A chart tag only sticks where the force has charted, and a headless server has
  -- charted nothing - which is why the marker silently failed to appear.
  force.chart(character.surface, {{position.x - 32, position.y - 32},
                                  {position.x + 32, position.y + 32}})
  -- No is_chunk_charted guard here: it reports false on a headless server even where
  -- add_chart_tag works perfectly, so guarding on it meant the marker never appeared.
  -- Through a closure, not pcall(force.add_chart_tag, force, ...): these methods come
  -- back already bound to their object, so passing the force again shifts every
  -- argument along one and the call quietly returns nil.
  storage.marker = force.add_chart_tag(character.surface,
                                       {position = position,
                                        text = storage.name or "Bonsai"})
end

script.on_event(defines.events.on_tick, function()
  local character = storage.bot
  if not (character and character.valid) then return end
  if game.tick % 60 == 0 then update_marker(character) end

  local path = storage.path
  if not path then return end

  local node = path[storage.node or 1]
  if not node then stop_walking(character) return end

  local here = character.position
  if distance(here, node.position) < NODE_REACHED then
    storage.node = (storage.node or 1) + 1
    if storage.node > #path then
      storage.arrived = true
      stop_walking(character)
      return
    end
    node = path[storage.node]
    -- The next waypoint is further away than the one just reached, so the "am I
    -- getting closer" measurement has to start again. Without this it walked
    -- perfectly and declared itself stuck every three seconds.
    storage.closest, storage.since = nil, nil
  end

  -- Not getting closer for three seconds means something is in the way that the path
  -- did not know about. Saying so beats walking into a rock until the turn budget runs
  -- out, which is exactly what the screen-watching version used to do.
  local gap = distance(here, node.position)
  if storage.closest == nil or gap < storage.closest - 0.1 then
    storage.closest, storage.since = gap, game.tick
  elseif game.tick - (storage.since or game.tick) > STUCK_TICKS then
    storage.stuck = true
    stop_walking(character)
    return
  end

  character.walking_state = {walking = true,
                             direction = direction_towards(here, node.position)}
end)

script.on_event(defines.events.on_script_path_request_finished, function(event)
  if event.id ~= storage.request then return end
  storage.request = nil
  if not event.path then
    storage.stuck = true
    storage.unreachable = true
    return
  end
  storage.path, storage.node = event.path, 1
  storage.closest, storage.since = nil, nil
end)

remote.add_interface("bonsai", {
  -- So the bridge can tell Bonsai's body from anyone else's. Every action used to
  -- take "the first character on the surface", which once a person joined was a coin
  -- flip between Bonsai and them - and mining from someone else's hands looks exactly
  -- like your inventory emptying itself.
  body_id = function()
    local character = storage.bot
    return (character and character.valid) and character.unit_number or nil
  end,

  spawn = function(name)
    storage.name = name or "Bonsai"
    local character = ensure_body()
    label(character)
    update_marker(character)
    return {x = character.position.x, y = character.position.y}
  end,

  walk_to = function(x, y)
    local character = ensure_body()
    storage.stuck, storage.unreachable, storage.arrived = false, false, false
    stop_walking(character)
    storage.goal = {x = x, y = y}
    -- entity_to_ignore is the character itself: without it the pathfinder finds the
    -- start position occupied by the very thing it is routing, and returns no path
    -- even across six tiles of open ground.
    storage.request = character.surface.request_path{
      bounding_box = {{-0.4, -0.4}, {0.4, 0.4}},
      collision_mask = {layers = {player = true, train = true}},
      start = character.position,
      goal = {x, y},
      force = character.force,
      radius = ARRIVED,
      entity_to_ignore = character,
      path_resolution_modifier = -1,
    }
    return true
  end,

  -- Polled by the bridge rather than pushed, because a mod cannot open a socket.
  state = function()
    local character = storage.bot
    if not (character and character.valid) then return {alive = false} end
    local position = character.position
    local left = storage.goal and distance(position, storage.goal) or 0
    return {
      alive = true,
      x = position.x, y = position.y,
      walking = storage.path ~= nil,
      arrived = storage.arrived == true or (storage.goal ~= nil and left <= ARRIVED),
      remaining = left,
      stuck = storage.stuck or false,
      unreachable = storage.unreachable or false,
      -- Reported from in here because a mod's storage is not the scenario's: reading
      -- storage.marker over RCON reads the console's own table and always says nil,
      -- which cost an hour of chasing a bug that was not there.
      named = storage.tag ~= nil and storage.tag.valid,
      on_map = storage.marker ~= nil and storage.marker.valid,
    }
  end,

  stop = function()
    stop_walking(storage.bot)
    return true
  end,
})
