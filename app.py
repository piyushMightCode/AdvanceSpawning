from flask import Flask, request, jsonify
import random
from collections import defaultdict

app = Flask(__name__)

sessions = {}
active_id = None


SHOP_TRIES = 50
COLORS = ["yellow", "red", "blue", "cyan", "lime", "gray", "purple"]


def parse_pos(key):
    if isinstance(key, tuple):
        return key
    if isinstance(key,list):
        return (key[0],key[1])
    x, z = key.strip("[]").split(",")
    return float(x), float(z)


def is_valid_tile(world, pos):
    return pos in world and world[pos]["type"] == "empty"


def is_valid_or_road(world, pos):
    return pos in world and world[pos]["type"] in ("empty", "road")


SHOP_SHAPES = {
    0: {"tiles": [(0, 0), (2, 0), (0, 2), (2, 2), (0, 4), (2, 4)], "road": (4, 2)},
    1: {"tiles": [(0, 0), (2, 0), (0, 2), (2, 2), (0, 4), (2, 4)], "road": (-2, 2)},
    2: {"tiles": [(0, 0), (2, 0), (0, 2), (2, 2), (4, 0), (4, 2)], "road": (2, 4)},
    3: {"tiles": [(0, 0), (2, 0), (0, 2), (2, 2), (4, 0), (4, 2)], "road": (2, -2)},
}


def is_valid_shop_placement(world, x, z, shape):
    for dx, dz in shape["tiles"]:
        pos = (x + dx, z + dz)
        if pos not in world or world[pos]["type"] != "empty":
            return False

    rx, rz = shape["road"]
    road_pos = (x + rx, z + rz)

    if road_pos not in world:
        return False

    return world[road_pos]["type"] in ("empty", "road")


def pick_shop(world):
    keys = [k for k, v in world.items() if v["type"] == "empty"]

    for _ in range(SHOP_TRIES):
        x, z = random.choice(keys)
        o = random.randint(0, 3)
        shape = SHOP_SHAPES[o]

        if not is_valid_shop_placement(world, x, z, shape):
            continue

        return ((x, z), random.choice(COLORS), o)

    return None


def has_adjacent_space(world, x, z):
    for dx, dz in [(2, 0), (-2, 0), (0, 2), (0, -2)]:
        if is_valid_tile(world, (x + dx, z + dz)):
            return True
    return False


def find_valid_nearby(world, base):
    bx, bz = base
    candidates = []

    for dx in range(-4, 5, 2):
        for dz in range(-4, 5, 2):
            x, z = bx + dx, bz + dz

            if not is_valid_tile(world, (x, z)):
                continue

            if not has_adjacent_space(world, x, z):
                continue

            candidates.append((x, z))

    return random.choice(candidates) if candidates else None


def compute_demand(shops, score):
    demand = defaultdict(float)

    for s in shops.values():
        upgraded = s.get("upgraded", False)
        timer = s.get("timer", 0)

        if upgraded:
            base = 3 + (1.5 * timer)
            bonus = (1 if score >= 1000 else 0) + (1 if score >= 2000 else 0)
        else:
            base = 2 + timer
            bonus = (0.5 if score >= 1000 else 0) + (0.5 if score >= 2000 else 0)

        demand[s["color"]] += base + bonus

    return demand


def compute_supply(world):
    supply = defaultdict(int)

    for v in world.values():
        if v["type"] == "house":
            supply[v["color"]] += 1

    return supply


def get_shortage(demand, supply):
    shortage = {}
    total = 0

    for c, d in demand.items():
        diff = max(0, d - supply.get(c, 0))
        if diff > 0:
            shortage[c] = diff
            total += diff

    return shortage, total


def parse_cluster(v):
    used, limit = v[0].split("/")
    return int(used), int(limit), v[1]


def is_full_cluster(v):
    used, limit, _ = parse_cluster(v)
    return used >= limit


def update_cluster(clusters, key):
    used, limit, color = parse_cluster(clusters[key])
    clusters[key] = [f"{used + 1}/{limit}", color]
    return clusters


def force_full(clusters, key):
    used, limit, color = parse_cluster(clusters[key])
    clusters[key] = [f"{limit}/{limit}", color]
    return clusters


def kill_cluster(clusters, key):
    return force_full(clusters, key)


def create_new_cluster(clusters, world, color):
    keys = [k for k, v in world.items() if v["type"] == "empty"]

    for _ in range(25):
        x, z = random.choice(keys)
        key = f"[{x},{z}]"

        if key in clusters:
            continue

        if not is_valid_tile(world, (x, z)):
            continue
        limit = random.randint(4,8)

        clusters[key] = ["0/"+str(limit), color]
        return clusters

    return clusters


def pick_house(world, shops, clusters, score):
    demand = compute_demand(shops, score)
    supply = compute_supply(world)
    shortage, total = get_shortage(demand, supply)

    if total == 0:
        return None, clusters, False

    r = random.uniform(0, total)
    acc = 0
    color = None

    for c, v in shortage.items():
        acc += v
        if r <= acc:
            color = c
            break

    valid = [k for k, v in clusters.items() if v[1] == color and not is_full_cluster(v)]

    use_new = (not valid) or random.random() >= 0.85

    if use_new:
        if random.random() < 0.15 or not valid:
            clusters = create_new_cluster(clusters, world, color)

        valid = [k for k, v in clusters.items() if v[1] == color and not is_full_cluster(v)]

        if not valid:
            return None, clusters, False

    cluster_key = random.choice(valid)

    for _ in range(50):
        base = parse_pos(cluster_key)

        if cluster_key not in clusters:
            break

        if is_full_cluster(clusters[cluster_key]):
            kill_cluster(clusters, cluster_key)
            if cluster_key in valid:
                valid.remove(cluster_key)
            if not valid:
                break
            cluster_key = random.choice(valid)
            continue

        pos = find_valid_nearby(world, base)

        if pos is None:
            kill_cluster(clusters, cluster_key)
            if cluster_key in valid:
                valid.remove(cluster_key)
            if not valid:
                break
            cluster_key = random.choice(valid)
            continue

        clusters = update_cluster(clusters, cluster_key)
        return ((pos[0], pos[1]), color), clusters, False

    return None, clusters, True


@app.route("/spawn", methods=["POST"])
def spawn():
    global active_id

    data = request.get_json(force=True)

    sid = str(data.get("id", ""))

    if not sid:
        return jsonify({"error": "missing id"}), 400

    if active_id is not None and active_id != sid:
        sessions.clear()

    active_id = sid

    if sid not in sessions:
        sessions[sid] = {}

    session = sessions[sid]

    if "worldkey" in data:
        session["worldkey"] = data["worldkey"]

    if "worldvalue" in data:
        session["worldvalue"] = data["worldvalue"]

    if "clusters" in data:
        session["clusters"] = data["clusters"]

    if "shopskey" in data:
        session["shopskey"] = data["shopskey"]

    if "shopvalue" in data:
        session["shopvalue"] = data["shopvalue"]

    if "score" in data:
        session["score"] = data["score"]

    required = ["worldkey", "worldvalue", "clusters", "shopskey"]

    missing = [x for x in required if x not in session]

    if missing:
        return jsonify({
            "status": "waiting",
            "missing": missing
        })

    keys = session["worldkey"]
    values = session["worldvalue"]

    shopkey = session["shopskey"]
    shopsvalue = session["shopvalue"]

    world = {parse_pos(k): v for k, v in zip(keys, values)}

    shops = {parse_pos(k): v for k, v in zip(shopkey, shopsvalue)}
    clusters = session["clusters"]
    score = session.get("score", 0)

    sessions.pop(sid, None)

    house_result, clusters, expand = pick_house(world, shops, clusters, score)

    if house_result:
        return jsonify({
            "upgrade": 0,
            "shop": 0,
            "house": {
                "position": list(house_result[0]),
                "color": house_result[1]
            },
            "clusters": clusters,
            "expand": False
        })

    if expand:
        return jsonify({
            "upgrade": 0,
            "shop": 0,
            "house": 0,
            "clusters": clusters,
            "expand": True
        })

    available_upgrades = [
        k for k, v in shops.items()
        if not v.get("upgraded", False)
    ]

    if available_upgrades and random.random() < 0.25:
        upgrade_target = random.choice(available_upgrades)
        shop = shops[upgrade_target]
        shop["upgraded"] = True

        return jsonify({
            "upgrade": 1,
            "shop": {
                "position": list(parse_pos(upgrade_target)),
                "color": shop["color"],
                "orientation": shop.get("orientation", 0),
                "upgraded": True
            },
            "house": 0,
            "clusters": clusters,
            "expand": False
        })

    shop_result = pick_shop(world)

    if shop_result:
        return jsonify({
            "upgrade": 0,
            "shop": {
                "position": list(shop_result[0]),
                "color": shop_result[1],
                "orientation": shop_result[2]
            },
            "house": 0,
            "clusters": clusters,
            "expand": False
        })

    if available_upgrades and random.random() < 0.30:
        upgrade_target = random.choice(available_upgrades)
        shop = shops[upgrade_target]
        shop["upgraded"] = True

        return jsonify({
            "upgrade": 1,
            "shop": {
                "position": list(parse_pos(upgrade_target)),
                "color": shop["color"],
                "orientation": shop.get("orientation", 0),
                "upgraded": True
            },
            "house": 0,
            "clusters": clusters,
            "expand": False
        })

    return jsonify({
        "upgrade": 0,
        "shop": 0,
        "house": 0,
        "clusters": clusters,
        "expand": True
    })


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
